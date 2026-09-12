"""文件系统公共工具：UTF-8 读取（含编码探测）、简评文件查找。

被 scanner / tagging 共用（消除两处重复实现）。
"""
import os


def read_utf8(path: str) -> str:
    """按 utf-8-sig / gbk / utf-8 / big5 顺序探测解码读文件。失败返回空串。"""
    for enc in ("utf-8-sig", "gbk", "utf-8", "big5"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""


def find_jianping_files(roots) -> list:
    """在 roots 下递归查找「简评」txt 文件，返回绝对路径列表。"""
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if "简评" in fn and fn.lower().endswith(".txt"):
                    found.append(os.path.join(dirpath, fn))
    return found
