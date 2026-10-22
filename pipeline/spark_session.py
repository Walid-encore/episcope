"""The one place that creates the project's SparkSession (generator, bronze, silver, gold).

Memory is set here in code, so the pipeline behaves the same for anyone who follows the
README: no environment variable is needed. By default the driver gets 40% of the machine's
RAM, clamped to 2-8 GB (an 8 GB laptop -> 3 GB, a 16 GB laptop -> 6 GB). Pass driver_memory
(for example "6g") to override. The memory setting only takes effect when this call creates
the session; an already-running session keeps its old settings.
"""
from __future__ import annotations

import os
import sys

DEFAULT_SHUFFLE_PARTITIONS = 64


def total_ram_bytes():
    try:
        if sys.platform == "win32":
            import ctypes

            class MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MemStatus()
            m.dwLength = ctypes.sizeof(MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return int(m.ullTotalPhys)
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:
        return None


def default_driver_memory(ram_bytes=None) -> str:
    ram = total_ram_bytes() if ram_bytes is None else ram_bytes
    if not ram:
        return "3g"
    gb = int(ram / 2 ** 30 * 0.4)
    return f"{min(8, max(2, gb))}g"


def get_spark(driver_memory=None, shuffle_partitions=DEFAULT_SHUFFLE_PARTITIONS, tmp_dir=None,
              app_name="episcope"):
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    from pyspark.sql import SparkSession
    b = (SparkSession.builder.appName(app_name).master("local[*]")
         .config("spark.driver.memory", driver_memory or default_driver_memory())
         .config("spark.sql.shuffle.partitions", str(shuffle_partitions))
         .config("spark.sql.session.timeZone", "UTC")
         .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
         .config("spark.sql.adaptive.enabled", "true")
         .config("spark.hadoop.mapreduce.fileoutputcommitter.marksuccessfuljobs", "false")
         .config("spark.ui.showConsoleProgress", "false"))
    if tmp_dir:
        b = b.config("spark.local.dir", str(tmp_dir))
    return b.getOrCreate()
