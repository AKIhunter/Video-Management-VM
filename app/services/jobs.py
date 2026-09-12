"""统一长时间任务运行器：扫描 / 全量联网补全 共用。

保证任意时刻只有一个长任务在跑（互斥）；任务对象提供进度、取消能力，
由被调用的工作函数在「分块边界」检查 stop 事件实现温和取消。
"""
import threading


class Job:
    def __init__(self, kind: str, fn, **meta):
        self.kind = kind          # 'scan' / 'completion' ...
        self.fn = fn             # fn(job) -> dict（返回结果）
        self.meta = meta          # scope/target 等附加信息，原样进 status
        self._stop = threading.Event()
        self._thread = None
        self._running = False
        self._done = 0
        self._total = 0
        self._current = None
        self._result = None
        self._error = None
        self._canceled = False

    @property
    def stop(self) -> threading.Event:
        return self._stop

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._result = self.fn(self)
        except Exception as e:  # noqa: BLE001
            self._error = f"{e}"
        finally:
            self._running = False

    def cancel(self) -> None:
        self._stop.set()

    def begin(self, total: int, current=None) -> None:
        self._total = total
        self._done = 0
        self._current = current

    def tick(self, done: int, total=None, current=None) -> None:
        self._done = done
        if total is not None:
            self._total = total
        if current is not None:
            self._current = current

    def status(self) -> dict:
        return {
            "kind": self.kind,
            **self.meta,
            "running": self._running,
            "cancelable": self._running,
            "done": self._done,
            "total": self._total,
            "current": self._current,
            "canceled": self._canceled or self._stop.is_set(),
            "result": self._result,
            "error": self._error,
        }


_lock = threading.Lock()
_current: Job | None = None


def current() -> Job | None:
    with _lock:
        return _current


def run_exclusive(job: Job) -> dict:
    """互斥启动：已有运行中的任务则拒绝。"""
    global _current
    with _lock:
        if _current is not None and _current.is_running:
            return {"started": False, "reason": f"{_current.kind} 任务进行中"}
        _current = job
    job.start()
    return {"started": True}