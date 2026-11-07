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

from core.config import PATHS
from core.shared.logger import get_logger

_LOGGER = get_logger(__name__)

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
    """判断单个 cron 字段是否匹配给定值。

    Args:
        field: cron 字段（* / 步长 / 列表 / 范围 / 单值）。
        value: 待匹配的时间值。

    Returns:
        是否匹配。
    """
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
    """判断 5 字段 cron 表达式是否匹配给定时间。

    Args:
        cron_expression: 5 字段 cron 表达式。
        dt: 待判断的时间。

    Returns:
        是否命中；字段数不是 5 时返回 False。
    """
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
    """校验单个 cron 字段。

    Args:
        field: cron 字段。
        low: 允许的下界。
        high: 允许的上界。

    Returns:
        错误信息；合法时返回 None。
    """
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
    """校验 5 字段 cron 表达式。

    Args:
        cron_expression: 待校验的表达式。

    Returns:
        错误信息；合法时返回 None。
    """
    fields = cron_expression.strip().split()
    if len(fields) != 5:
        return f"Invalid cron expression. Expect 5 fields, got {len(fields)}: {cron_expression}"

    bounds = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    names = ["minute", "hour", "day-of-month", "month", "day-of-week"]
    for field, (low, high), name in zip(fields, bounds, names):
        err = _validate_cron_field(field, low, high)
        if err:
            return f"{name}: {err}"

    return None


def save_durable_jobs() -> None:
    """把 durable 任务写入磁盘。"""
    durable = [asdict(job) for job in SCHEDULED_JOBS.values() if job.durable]
    PATHS.CRON_TASK_FILE.write_text(json.dumps(durable, indent=4), encoding="utf-8")


def load_durable_jobs() -> None:
    """从磁盘加载 durable 任务；文件缺失时静默返回，读取 / 解析失败时记警告并跳过。"""
    if not PATHS.CRON_TASK_FILE.exists():
        return

    try:
        for job_data in json.loads(PATHS.CRON_TASK_FILE.read_text(encoding="utf-8")):
            job = CronJob(**job_data)
            if not validate_cron(job.cron):
                SCHEDULED_JOBS[job.id] = job
    except Exception as e:
        _LOGGER.warning(f"[Cron] load durable jobs failed: {e}")


def schedule_job(
    cron_expression: str,
    prompt: str,
    recurring: bool = True,
    durable: bool = True,
) -> CronJob | str:
    """登记一个 cron 任务并可选落盘。

    Args:
        cron_expression: 5 字段 cron 表达式。
        prompt: 每次触发时注入的提示词。
        recurring: 是否重复触发。
        durable: 是否跨会话持久化。

    Returns:
        新建的 CronJob；表达式非法时返回错误信息。
    """
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
    """按 id 取消任务；durable 任务同步更新磁盘。

    Args:
        job_id: 任务 id。

    Returns:
        可直接展示的结果文本。
    """
    with CRON_LOCK:
        job = SCHEDULED_JOBS.pop(job_id, None)

    if not job:
        return f"Job {job_id} not found"

    if job.durable:
        save_durable_jobs()

    return f"Job {job_id} cancelled"


def cron_scheduler_loop() -> None:
    """调度线程主循环：每秒检查一次到期任务并入队。"""
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
                    _LOGGER.warning(f"[Cron error] {job.id}: {e}")


def consume_cron_queue() -> list[CronJob]:
    """取出并清空本轮到期的任务。

    Returns:
        到期任务列表。
    """
    with CRON_LOCK:
        fired_jobs = list(CRON_QUEUE)
        CRON_QUEUE.clear()
    return fired_jobs


def run_schedule_cron(
    cron_expression: str,
    prompt: str,
    recurring: bool = True,
    durable: bool = True,
) -> str:
    """工具层入口：登记 cron 任务并返回确认文本。

    Args:
        cron_expression: 5 字段 cron 表达式。
        prompt: 每次触发时注入的提示词。
        recurring: 是否重复触发。
        durable: 是否跨会话持久化。

    Returns:
        任务登记结果文本。

    Raises:
        Exception: cron 表达式非法。
    """
    result = schedule_job(
        cron_expression,
        prompt,
        recurring,
        durable,
    )
    if isinstance(result, str):
        raise Exception(result)

    return f"Scheduled jobs:\njob id: {result.id}, '{cron_expression}' -> {prompt}"


def run_list_crons() -> str:
    """工具层入口：列出全部 cron 任务。

    Returns:
        任务列表文本；无任务时返回 "No jobs scheduled"。
    """
    with CRON_LOCK:
        jobs = list(SCHEDULED_JOBS.values())
    if not jobs:
        return "No jobs scheduled"
    return "\n".join(f"  {job.id}: '{job.cron}' -> {job.prompt[:40]} "
                     f"[{'recurring' if job.recurring else 'one-shot'}, "
                     f"{'durable' if job.durable else 'session'}]" for job in jobs)


def run_cancel_cron(job_id: str) -> str:
    """工具层入口：按 id 取消任务。

    Args:
        job_id: 任务 id。

    Returns:
        取消结果文本。
    """
    return cancel_job(job_id)


def start_cron_scheduler() -> None:
    """加载持久化任务并启动调度线程；由 core.bootstrap 在启动时调用。"""
    load_durable_jobs()
    threading.Thread(target=cron_scheduler_loop, daemon=True).start()
