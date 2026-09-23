"""在线更新：检查最新版本、下载发布包、覆盖程序目录。

只用标准库（``urllib`` 走 HTTPS、``zipfile`` 解包），不调用 cmd / powershell，
也不会弹出任何系统窗口。

下载源有三个：

* 官方源 ``github.com``（直连）；
* ``gh-proxy.com`` 的 CloudFlare 优选 IPv4 节点（``v4.gh-proxy.org``，推荐）；
* ``gh-proxy.com`` 的 CloudFlare 全球节点（``gh-proxy.org``）。

加速源只是把 GitHub 的完整地址接在加速域名后面，例如：

    https://v4.gh-proxy.org/https://github.com/<仓库>/releases/download/v2.4.1/<包名>.zip

覆盖更新时只替换程序目录里的文件（先写临时文件、再原子改名），
``SecureVaultData`` 数据目录与用户自己的文件不会被碰到。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import ssl
import sys
import time
import urllib.error
import urllib.request
import zipfile

from .. import APP_VERSION, REPO_SLUG
from ..core.fileutil import human_size

#: 查最新版本用的 GitHub API（走加速源时会把完整地址拼在加速域名后面）
API_URL = "https://api.github.com/repos/%s/releases/latest" % REPO_SLUG
#: 发布包地址模板（依次填入版本号、文件名）
DOWNLOAD_URL = "https://github.com/%s/releases/download/v%%s/%%s" % REPO_SLUG
#: 发布页（浏览器里看更新说明）
RELEASES_PAGE = "https://github.com/%s/releases" % REPO_SLUG

USER_AGENT = "SecureVault/%s" % APP_VERSION
CHUNK = 256 * 1024
TIMEOUT = 30

#: 覆盖更新时**不**写入程序目录的东西（数据目录、说明文件、缓存）
SKIP_NAMES = {"请先读我.txt", "readme.txt", "readme.md", "license", "license.txt"}
SKIP_PREFIXES = ("securevaultdata", "__pycache__", ".git")
#: 更新包里必须有这个文件，否则拒绝覆盖（避免用错包把程序覆盖坏）
REQUIRED_FILE = "SecureVault.exe"
#: 随程序分发的 CA 证书包（放在 assets 里）：系统证书库缺根证书时用它
CA_BUNDLE_NAME = "cacert.pem"


class UpdateError(Exception):
    """检查 / 下载 / 覆盖过程中的可预期错误，消息可以直接显示给用户。"""


# ---------------------------------------------------------------------------
# 下载源
# ---------------------------------------------------------------------------

#: (键, 界面文字, 加速前缀)
SOURCES = (
    ("github", "官方源（github.com）", ""),
    ("v4", "gh-proxy.com（CloudFlare-优选-IPv4）（推荐）", "https://v4.gh-proxy.org/"),
    ("global", "gh-proxy.com（CloudFlare 全球）", "https://gh-proxy.org/"),
)
#: 默认线路是官方源（检查与下载都用它，除非用户自己改）
DEFAULT_SOURCE = "github"
#: 兼容旧名字：检查最新版本默认也走官方源
CHECK_SOURCE = DEFAULT_SOURCE

#: 发布说明里「本版改动」那一段的标题关键字（用它把更新内容挑出来）
CHANGE_HEADINGS = ("本版主要改动", "本版改动", "主要改动", "更新内容", "更新说明",
                   "更新日志", "改动", "changelog", "what's changed")


def source_options():
    """给下拉框用的 [(键, 文字), ...]。"""
    return [(key, label) for key, label, _prefix in SOURCES]


def source_info(key: str):
    for item in SOURCES:
        if item[0] == key:
            return item
    return SOURCES[0]


def source_label(key: str) -> str:
    return source_info(key)[1]


def source_prefix(key: str) -> str:
    return source_info(key)[2]


def build_url(key: str, url: str) -> str:
    """按下载源拼出最终地址（加速源 = 前缀 + 原始地址）。"""
    return source_prefix(key) + url


def download_url(version: str, asset: str, source: str = DEFAULT_SOURCE) -> str:
    return build_url(source, DOWNLOAD_URL % (version, asset))


def release_url(version: str, asset: str) -> str:
    """官方地址（界面上显示用，方便手动核对 / 手动下载）。"""
    return DOWNLOAD_URL % (version, asset)


# ---------------------------------------------------------------------------
# 版本号比较
# ---------------------------------------------------------------------------

def version_tuple(text: str):
    """把 ``v2.4.1`` / ``2.4`` 之类转成可比较的数字元组。"""
    numbers = re.findall(r"\d+", str(text or ""))
    values = tuple(int(item) for item in numbers[:4])
    return values or (0,)


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    return version_tuple(latest) > version_tuple(current)


# ---------------------------------------------------------------------------
# HTTPS 证书
# ---------------------------------------------------------------------------

def ca_bundle_path() -> str:
    """随程序分发的 CA 证书包路径（找不到返回空串）。"""
    candidates = []
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    candidates.append(os.path.join(root, "assets", CA_BUNDLE_NAME))
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        for base in (getattr(sys, "_MEIPASS", ""), exe_dir,
                     os.path.join(exe_dir, "_internal")):
            if base:
                candidates.insert(0, os.path.join(base, "assets", CA_BUNDLE_NAME))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def ssl_context(insecure: bool = False):
    """返回 HTTPS 用的 SSLContext。

    默认**优先用随程序分发的 CA 证书包**（`assets\\cacert.pem`）：有些电脑的系统
    证书库缺根证书（或者从没更新过），直接用系统证书库会报 SSL 校验失败。
    ``insecure=True`` 时不校验证书（只用于"证书问题的应急重查"，界面上会明确提示）。
    """
    if insecure:
        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return context
        except Exception:
            return None
    bundle = ca_bundle_path()
    if bundle:
        try:
            return ssl.create_default_context(cafile=bundle)
        except Exception:
            pass
    try:
        return ssl.create_default_context()
    except Exception:
        return None


def is_ssl_error(exc: BaseException) -> bool:
    """判断异常链里有没有证书 / TLS 相关错误。"""
    seen = 0
    node = exc
    while node is not None and seen < 10:
        if isinstance(node, ssl.SSLError):
            return True
        text = str(node).lower()
        for key in ("certificate", "ssl", "tls"):
            if key in text:
                return True
        node = getattr(node, "__cause__", None) or getattr(node, "reason", None)
        seen += 1
    return False


def changes_only(text: str) -> str:
    """只取发布说明里「本版主要改动」这一段（没有这一段时返回空串）。"""
    lines = (text or "").splitlines()
    start = -1
    level = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        title = stripped.lstrip("#").strip().strip("*").strip().lower()
        if any(key in title for key in CHANGE_HEADINGS):
            start = index + 1
            level = len(stripped) - len(stripped.lstrip("#"))
            break
    if start < 0:
        return ""
    collected = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            depth = len(stripped) - len(stripped.lstrip("#"))
            if depth <= level:
                break
        collected.append(line)
    return "\n".join(collected).strip()


# ---------------------------------------------------------------------------
# 检查更新
# ---------------------------------------------------------------------------

def check_for_update(source: str = DEFAULT_SOURCE, timeout: int = TIMEOUT,
                     insecure: bool = False) -> dict:
    """查最新发布；返回版本 / 更新说明 / 下载地址等信息（不下载）。

    出错时抛 :class:`UpdateError`，消息可以直接显示给用户。
    """
    url = build_url(source, API_URL)
    data = _fetch_json(url, timeout, insecure=insecure)
    tag = str(data.get("tag_name") or data.get("name") or "").strip()
    latest = tag.lstrip("vV").strip() or APP_VERSION
    notes = str(data.get("body") or "").strip()
    asset_name, asset_size = _pick_asset(data.get("assets") or [], latest)
    return {
        "source": source,
        "source_label": source_label(source),
        "latest": latest,
        "tag": tag or ("v%s" % latest),
        "name": str(data.get("name") or "").strip(),
        "notes": notes,
        "published": str(data.get("published_at") or "")[:10],
        "asset": asset_name,
        "size": asset_size,
        "newer": is_newer(latest),
        "url": download_url(latest, asset_name, source),
        "official_url": release_url(latest, asset_name),
        "page": RELEASES_PAGE,
    }


def _pick_asset(assets, latest: str):
    """从发布附件里挑出便携版压缩包（优先带本次版本号的）。"""
    fallback = ""
    fallback_size = 0
    for item in assets:
        name = str(item.get("name") or "")
        if not name.lower().endswith(".zip"):
            continue
        size = int(item.get("size") or 0)
        if latest and latest in name:
            return name, size
        if not fallback:
            fallback, fallback_size = name, size
    if fallback:
        return fallback, fallback_size
    # 附件还没传：按命名规则推一个（大概率就是它）
    return "SecureVault-%s-win64-portable.zip" % latest, 0


def _fetch_json(url: str, timeout: int = TIMEOUT, insecure: bool = False) -> dict:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=ssl_context(insecure)) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("没有找到发布信息（HTTP 404）：仓库或发布页可能还没建好。")
        if exc.code in (403, 429):
            raise UpdateError("服务器拒绝了这次请求（HTTP %s）：可能是访问太频繁，"
                              "稍后再试或换个下载源。" % exc.code)
        raise UpdateError("服务器返回错误：HTTP %s。" % exc.code)
    except urllib.error.URLError as exc:
        if is_ssl_error(exc):
            raise UpdateError(
                "HTTPS 证书校验失败：%s\n"
                "常见原因：电脑的系统时间不对（日期/时区），或者网络里有代理、"
                "防火墙替换了证书。可以点「忽略证书校验重查一次」应急，"
                "或者换一个线路再试。" % getattr(exc, "reason", exc))
        raise UpdateError("连接失败：%s。请检查网络，或换一个下载源再试。"
                          % getattr(exc, "reason", exc))
    except ssl.SSLError as exc:
        raise UpdateError(
            "HTTPS 证书校验失败：%s\n常见原因：电脑的系统时间不对（日期/时区），"
            "或者网络里有代理、防火墙替换了证书。可以点「忽略证书校验重查一次」应急，"
            "或者换一个线路再试。" % exc)
    except Exception as exc:
        raise UpdateError("连接失败：%s" % exc)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise UpdateError("返回的内容无法解析（可能是网络中间页）：%s" % exc)


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------

def download(url: str, dest: str, progress=None, timeout: int = TIMEOUT) -> str:
    """把 ``url`` 下载到 ``dest``（先写 ``.part``，成功后改名）。

    ``progress(已下载字节, 总字节)`` 会被反复调用（总字节未知时为 0）。
    下载一律做证书校验（不接受不安全的连接）。
    """
    folder = os.path.dirname(dest)
    if folder:
        os.makedirs(folder, exist_ok=True)
    part = dest + ".part"
    done = False
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=ssl_context(False)) as response:
            total = int(response.headers.get("Content-Length") or 0)
            written = 0
            last = 0.0
            with open(part, "wb") as handle:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    handle.write(block)
                    written += len(block)
                    now = time.time()
                    if progress is not None and now - last > 0.2:
                        last = now
                        _safe(progress, written, total)
        done = True
    except urllib.error.HTTPError as exc:
        raise UpdateError("下载失败：HTTP %s。" % exc.code)
    except urllib.error.URLError as exc:
        if is_ssl_error(exc):
            raise UpdateError("下载失败：HTTPS 证书校验失败（%s）。"
                              "可以换一个线路再试。" % getattr(exc, "reason", exc))
        raise UpdateError("下载失败：%s。可以换一个下载源再试。"
                          % getattr(exc, "reason", exc))
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError("下载失败：%s" % exc)
    finally:
        if not done:
            _remove(part)
    if not os.path.exists(part) or os.path.getsize(part) <= 0:
        _remove(part)
        raise UpdateError("下载失败：没有收到任何数据。")
    _remove(dest)
    os.replace(part, dest)
    if progress is not None:
        _safe(progress, os.path.getsize(dest), os.path.getsize(dest))
    return dest


# ---------------------------------------------------------------------------
# 解包与覆盖
# ---------------------------------------------------------------------------

def stage(zip_path: str, work_dir: str, progress=None):
    """把更新包解到 ``work_dir\\staging``；返回 ``(解出目录, 文件数)``。

    压缩包里如果有一层顶层目录（发布包就是 ``SecureVault/...``），这里会自动去掉，
    这样解出来的目录结构与程序目录一一对应。
    """
    staging = os.path.join(work_dir, "staging")
    _rmtree(staging)
    os.makedirs(staging, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            prefix = _common_prefix(names)
            total = len(names)
            count = 0
            for index, name in enumerate(names):
                relative = name[len(prefix):] if prefix and name.startswith(prefix) else name
                relative = relative.replace("\\", "/").strip("/")
                if not relative or _skip(relative):
                    continue
                target = _safe_target(staging, relative)
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with archive.open(name) as source, open(target, "wb") as handle:
                    shutil.copyfileobj(source, handle, CHUNK)
                count += 1
                if progress is not None and index % 20 == 0:
                    _safe(progress, index + 1, total)
    except zipfile.BadZipFile as exc:
        raise UpdateError("更新包不是有效的 zip 文件：%s" % exc)
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError("解包失败：%s" % exc)
    if count <= 0:
        raise UpdateError("更新包里没有可用的文件。")
    if not os.path.isfile(os.path.join(staging, REQUIRED_FILE)):
        raise UpdateError("更新包里没有 %s，已取消覆盖（避免把程序覆盖坏）。" % REQUIRED_FILE)
    return staging, count


def apply_staging(staging: str, target: str, progress=None) -> dict:
    """把解好的文件覆盖到程序目录；返回 {replaced, added, failed, total}。

    正在使用的文件（程序本体、已加载的 dll）在 Windows 上不能直接覆盖，
    这里先把旧文件改名让位再写新文件；改掉的旧文件会在下次启动时清理。
    """
    stamp = time.strftime("%Y%m%d%H%M%S")
    files = []
    for dirpath, dirnames, filenames in os.walk(staging):
        dirnames[:] = [name for name in dirnames if not _skip(name + "/")]
        for name in filenames:
            if _skip(name):
                continue
            files.append(os.path.relpath(os.path.join(dirpath, name), staging))
    files.sort()
    result = {"replaced": 0, "added": 0, "failed": [], "total": len(files)}
    for index, relative in enumerate(files):
        source = os.path.join(staging, relative)
        dest = os.path.join(target, relative)
        parent = os.path.dirname(dest)
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
            if os.path.exists(dest):
                _copy_over(source, dest, stamp)
                result["replaced"] += 1
            else:
                shutil.copyfile(source, dest)
                result["added"] += 1
        except Exception as exc:
            result["failed"].append((relative, str(exc)))
        if progress is not None and index % 20 == 0:
            _safe(progress, index + 1, len(files))
    return result


def _copy_over(source: str, dest: str, stamp: str) -> None:
    """覆盖单个文件：写不动（被占用）就先改名让位。"""
    try:
        shutil.copyfile(source, dest)
        return
    except OSError:
        pass
    backup = "%s.old-%s" % (dest, stamp)
    _remove(backup)
    os.replace(dest, backup)
    shutil.copyfile(source, dest)


def cleanup_backups(target: str) -> int:
    """清掉上次覆盖更新留下的 ``*.old-*`` 旧文件；返回删除数量。"""
    removed = 0
    try:
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = [name for name in dirnames
                           if not name.lower().startswith("securevaultdata")
                           and name.lower() != "update"]
            for name in filenames:
                if ".old-" not in name:
                    continue
                path = os.path.join(dirpath, name)
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    # 旧文件可能带着只读属性（覆盖前是只读的），先去属性再删
                    try:
                        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                        os.remove(path)
                        removed += 1
                    except Exception:
                        pass
    except Exception:
        pass
    return removed


def restart(path: str) -> bool:
    """更新完成后启动新版本（不经过 cmd / powershell）。"""
    try:
        os.startfile(path)  # noqa: S606 - Windows 专用
        return True
    except Exception:
        return False


def describe_size(size: int) -> str:
    return human_size(size) if size else "大小未知"


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _safe(callback, *args) -> None:
    try:
        callback(*args)
    except Exception:
        pass


def _remove(path: str) -> None:
    try:
        if not os.path.exists(path):
            return
        try:
            os.remove(path)
            return
        except OSError:
            pass
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        os.remove(path)
    except Exception:
        pass


def _rmtree(path: str) -> None:
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _skip(relative: str) -> bool:
    """判断相对路径是否要被跳过（数据目录 / 说明文件 / 缓存）。"""
    text = relative.replace("\\", "/").strip("/")
    if not text:
        return True
    parts = [part for part in text.split("/") if part not in ("", ".")]
    for part in parts:
        lowered = part.lower()
        if lowered.startswith(SKIP_PREFIXES) or lowered in SKIP_NAMES:
            return True
    return False


def _common_prefix(names) -> str:
    """返回所有条目共同的顶层目录前缀（没有就返回空串）。"""
    prefix = ""
    for name in names:
        parts = name.replace("\\", "/").split("/")
        if len(parts) < 2:
            return ""
        top = parts[0] + "/"
        if not prefix:
            prefix = top
        elif prefix != top:
            return ""
    return prefix


def _safe_target(root: str, relative: str) -> str:
    """把相对路径拼成绝对路径，并挡住 ``..`` 之类的越界条目。"""
    target = os.path.abspath(os.path.join(root, relative.replace("/", os.sep)))
    root_abs = os.path.abspath(root)
    if target != root_abs and not target.startswith(root_abs + os.sep):
        raise UpdateError("更新包里的路径越界，已取消：%s" % relative)
    return target
