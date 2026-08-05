"""
Cron Scheduler

Cron jobs are stored separately from conversation history.
When a job fires, it becomes a scheduled prompt that is injected back into the same agent loop.
"""
import json
import random
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime

from src.config import WORKDIR

DURABLE_PATH = WORKDIR / ".scheduled_tasks.json"
SCHEDULED_JOBS: dict[str, "CronJob"] = {}
CRON_QUEUE: list["CronJob"] = []
CRON_LOCK = threading.Lock()
LAST_FIRED: dict[str, str] = {}


@dataclass
class CronJob:
    id: str
    cron: str
    prompt: str
    recurring: bool
    durable: bool


def _cron_field_matches(field: str, value: int) -> bool:
    if field == "*":
        return True

    if field.startswith("*/"):
        step = int(field[2:])
        return step > 0 and value % step == 0

    if "," in field:
        return any(_cron_field_matches(part.strip(), value) for part in field.split(","))

    if "-" in field:
        low, high = field.split("-", 1)
        return int(low) <= value <= int(high)

    return value == int(field)


def cron_matches(cron_expression: str, dt: datetime) -> bool:
    fields = cron_expression.strip().split()
    if len(fields) != 5:
        return False

    minute, hour, dom, month, dow = fields
    dow_val = (dt.weekday() + 1) % 7
    m = _cron_field_matches(minute, dt.minute)
    h = _cron_field_matches(hour, dt.hour)
    dom_ok = _cron_field_matches(dom, dt.day)
    month_ok = _cron_field_matches(month, dt.month)
    dow_ok = _cron_field_matches(dow, dow_val)

    if not (m and h and month_ok):
        return False
    if dom == "*" and dow == "*":
        return True
    if dom == "*":
        return dow_ok
    if dow == "*":
        return dom_ok
    return dom_ok or dow_ok


def _validate_cron_field(field: str, low: int, high: int) -> str | None:
    if field == "*":
        return None
    if field.startswith("*/"):
        step = field[2:]
        if not step.isdigit() or int(step) <= 0:
            return f"Invalid step: {field}"
        return None

    if "," in field:
        for part in field.split(","):
            err = _validate_cron_field(part.strip(), low, high)
            if err:
                return err
        return None

    if "-" in field:
        left, right = field.split("-", 1)
        if not left.isdigit() or not right.isdigit():
            return f"Invalid range: {field}"
        left, right = int(left), int(right)
        if left < low or left > high or right < low or right > high:
            return f"Range {field} out of range [{low}, {high}]"
        if left > right:
            return f"Range start > end: {field}"
        return None

    if not field.isdigit():
        return f"Invalid field: {field}"

    value = int(field)
    if value < low or value > high:
        return f"Value {field} out of range [{low}, {high}]"

    return None


def validate_cron(cron_expression: str) -> str | None:
    fields = cron_expression.strip().split()
    if len(fields) != 5:
        return f"Invalid cron expression. Expect 5 fields, got {len(fields)}: {cron_expression}"

    bounds = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    names = ["minute", "hour", "day-of-,month", "month", "day-of-week"]
    for field, (low, high), name in zip(fields, bounds, names):
        err = _validate_cron_field(field, low, high)
        if err:
            return f"{name}: {err}"

    return None


def save_durable_jobs():
    durable = [asdict(job) for job in SCHEDULED_JOBS.values() if job.durable]
    DURABLE_PATH.write_text(json.dumps(durable, indent=2), encoding="utf-8")


def load_durable_jobs():
    if not DURABLE_PATH.exists():
        return

    try:
        for job_data in json.loads(DURABLE_PATH.read_text(encoding="utf-8")):
            job = CronJob(**job_data)
            if not validate_cron(job.cron):
                SCHEDULED_JOBS[job.id] = job
    except Exception:
        pass


def schedule_job(cron_expression: str, prompt: str, recurring: bool = True, durable: bool = True) -> CronJob | str:
    err = validate_cron(cron_expression)
    if err:
        return err

    job = CronJob(
        id=f"cron-{random.randint(0, 1000):04d}",
        cron=cron_expression,
        prompt=prompt,
        recurring=recurring,
        durable=durable
    )
    with CRON_LOCK:
        SCHEDULED_JOBS[job.id] = job

    if durable:
        save_durable_jobs()

    return job


def cancel_job(job_id: str) -> str:
    with CRON_LOCK:
        job = SCHEDULED_JOBS.pop(job_id)

    if not job:
        return f"Job {job_id} not found"

    if job.durable:
        save_durable_jobs()

    return f"Job {job_id} cancelled"


def cron_scheduler_loop():
    while True:
        time.sleep(1)
        now = datetime.now()
        marker = now.strftime("%Y-%m-%d %H:%M")
        with CRON_LOCK:
            for job in list(SCHEDULED_JOBS.values()):
                try:
                    if cron_matches(job.cron, now) and LAST_FIRED.get(job.id) != marker:
                        CRON_QUEUE.append(job)
                        LAST_FIRED[job.id] = marker
                        if not job.recurring:
                            SCHEDULED_JOBS.pop(job.id)
                            if job.durable:
                                save_durable_jobs()
                except Exception as e:
                    print(f"  \033[31m[Cron error] {job.id}: {e}\033[0m")


def consume_cron_queue() -> list[CronJob]:
    with CRON_LOCK:
        fired_jobs = list(CRON_QUEUE)
        CRON_QUEUE.clear()
    return fired_jobs


def run_schedule_cron(cron_expression: str, prompt: str, recurring: bool = True, durable: bool = True) -> str:
    result = schedule_job(cron_expression, prompt, recurring, durable)
    if isinstance(result, str):
        return f"Error: {result}"

    return f"Scheduled jobs:\njob id: {result.id}, '{cron_expression}' -> {prompt}"


def run_list_crons() -> str:
    with CRON_LOCK:
        jobs = list(SCHEDULED_JOBS.values())
    if not jobs:
        return "No jobs scheduled"
    return "\n".join(f"  {job.id}: '{job.cron}' -> {job.prompt[:40]} "
                     f"[{'recurring' if job.recurring else 'one-shot'}, "
                     f"{'durable' if job.durable else 'session'}]" for job in jobs)


def run_cancel_cron(job_id: str) -> str:
    return cancel_job(job_id)


load_durable_jobs()
threading.Thread(target=cron_scheduler_loop, daemon=True).start()
