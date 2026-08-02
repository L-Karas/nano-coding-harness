import threading

from config import CLI_ACTIVE, READLINE_AVAILABLE, PROMPT

if READLINE_AVAILABLE:
    import readline


def terminal_print(text: str):
    if threading.current_thread() is threading.main_thread() or not CLI_ACTIVE:
        print(text)
        return

    line = ""
    if READLINE_AVAILABLE:
        try:
            line = readline.get_line_buffer()
        except Exception:
            pass

    print(f"\r\033[K{text}")
    print(PROMPT + line, end="", flush=True)
