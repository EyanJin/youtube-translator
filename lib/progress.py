"""Progress tracking utility for multi-step pipeline operations."""

import time


class ProgressTracker:
    """Track and display progress for batch operations.

    Usage:
        tracker = ProgressTracker(total=100, step_name="翻译")
        for i in range(100):
            do_work(i)
            tracker.update(i + 1)
        tracker.done()
    """

    def __init__(self, total, step_name="处理"):
        self.total = total
        self.step_name = step_name
        self.start_time = time.time()
        self.last_print_time = 0

    def update(self, current, extra=""):
        """Update progress. Prints at most once per second."""
        now = time.time()
        if now - self.last_print_time < 1.0 and current < self.total:
            return

        self.last_print_time = now
        elapsed = now - self.start_time
        pct = (current / self.total * 100) if self.total > 0 else 0

        # Estimate remaining time
        if current > 0 and current < self.total:
            rate = elapsed / current
            remaining = rate * (self.total - current)
            eta_str = _format_duration(remaining)
            elapsed_str = _format_duration(elapsed)
            line = (f"[{self.step_name}] {current}/{self.total} "
                    f"({pct:.0f}%) | 已耗时 {elapsed_str} | 剩余 ~{eta_str}")
        else:
            elapsed_str = _format_duration(elapsed)
            line = f"[{self.step_name}] {current}/{self.total} ({pct:.0f}%) | 已耗时 {elapsed_str}"

        if extra:
            line += f" | {extra}"

        print(line)

    def done(self):
        """Mark the operation as complete."""
        elapsed = time.time() - self.start_time
        elapsed_str = _format_duration(elapsed)
        print(f"[{self.step_name}] 完成，共耗时 {elapsed_str}")


def _format_duration(seconds):
    """Format seconds into a human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m{s}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h{m}m"
