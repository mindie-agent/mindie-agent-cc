"""Thin stdlib adapter to shared diagnostics. Import performs no I/O.

[Role]
Bridges Claude Code adapter failures to `mindie_diagnostics` and formats
user-visible `diagnostic_ref` warnings. An unavailable runtime emits only a
static stderr warning; it never maintains a second diagnostic store.

[Invariants]
- Import performs zero I/O.
- If diagnostic storage fails, emits a single stderr warning and preserves the
  original operation outcome unchanged.

[Timeouts & Locks]
- Fault recording uses local I/O only. Explicit reporter activation has a
  75-second shared budget within a 90-second caller, never inside short Hooks.

[Side-Effect Policy]
- Writes redacted diagnostic records under the MindIE state directory.
"""

from __future__ import annotations

import ast
import json
import os
import re
import signal
import stat
import shlex
import sys
from pathlib import Path

COMPONENT = "mindie-agent-cc"
HOST = "claude-code"
REPOSITORY = "mindie-agent/mindie-agent"
_STORAGE = "MindIE diagnostic storage unavailable; original outcome unchanged."
_warned = False


def _hex(value, length):
    if isinstance(value, str) and len(value) == length:
        if all(char in "0123456789abcdef" for char in value):
            return value
    return None


def _read_small(path):
    """Read bounded regular metadata."""
    try:
        path = Path(path)
        if not path.is_file():
            return None
        with path.open("rb") as stream:
            raw = stream.read(2049)
        if len(raw) > 2048:
            return None
        return raw.decode()
    except TimeoutError:
        raise
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def build_metadata():
    """Use package metadata, or the same generation's older install records."""
    here = Path(__file__).resolve().parent
    try:
        raw = _read_small(here / "diagnostic-build.json")
        if raw is not None:
            data = json.loads(raw)
        elif (here / "resources/plugin.json").is_file():
            manifest = json.loads(_read_small(here / "resources/plugin.json") or "null")
            data = {"version": manifest.get("version")} if isinstance(manifest, dict) else {}
        else:
            # Old updaters do not emit diagnostic-build.json. Their completed
            # Git generation and stamped host manifest are existing facts.
            # A native plugin tree keeps the same version in `.claude-plugin`.
            generation = here.parent
            if here.name == "scripts" and generation.parent.name == "generations":
                revision = _hex(generation.name, 40)
                marker = _read_small(generation / ".mindie-generation-complete")
                if not revision or marker is None or marker.strip() != revision:
                    return {}
                manifest = json.loads(_read_small(generation / "host-package" / ".claude-plugin" / "plugin.json") or "null")
                data = {"revision": revision}
                if isinstance(manifest, dict) and manifest.get("name") == "mindie-agent":
                    data["version"] = manifest.get("version")
            elif here.name == "scripts":
                manifest = json.loads(_read_small(generation / ".claude-plugin" / "plugin.json") or "null")
                if not isinstance(manifest, dict) or manifest.get("name") != "mindie-agent":
                    return {}
                data = {"version": manifest.get("version")}
            else:
                return {}
        if not isinstance(data, dict):
            return {}
        revision = _hex(data.get("revision"), 40)
        version = data.get("version")
        result = {"revision": revision} if revision else {}
        if isinstance(version, str) and re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,79}", version):
            result["version"] = version
        return result
    except TimeoutError:
        raise
    except (OSError, ValueError, UnicodeDecodeError, TypeError, KeyError):
        return {}


def _warn():
    global _warned
    if _warned:
        return
    _warned = True
    try:
        sys.stderr.write(_STORAGE + "\n")
    except Exception:
        pass


def _record():
    from mindie_diagnostics.integration import record_failure
    os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(reporting_config_path())
    return record_failure


def _project_build(data):
    if not isinstance(data, dict):
        return {}
    revision = _hex(data.get("revision"), 40)
    version = data.get("version")
    result = {"revision": revision} if revision else {}
    if isinstance(version, str) and re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,79}", version):
        result["version"] = version
    return result


def _invoke_record(record_fn, component, operation, *, stage, category, exception,
                   revision, version, elapsed_ms, exit_code, reportable,
                   source_root, evidence, reason_code, substage, infer_build_meta=None):
    base = dict(
        stage=stage, category=category, exception=exception, host=HOST,
        revision=revision, version=version, elapsed_ms=elapsed_ms,
        exit_code=exit_code, reportable=reportable,
    )
    extended = dict(base, source_root=source_root, evidence=evidence)
    v2 = dict(extended)
    if reason_code is not None:
        v2["reason_code"] = reason_code
    if substage is not None:
        v2["substage"] = substage
    if infer_build_meta is not None:
        v2["infer_build_meta"] = infer_build_meta
    try:
        return record_fn(component, operation, **v2)
    except TypeError:
        if infer_build_meta is False and getattr(record_fn, "__module__", "") == "mindie_diagnostics.integration":
            from mindie_diagnostics.fallback import record_failure as fb_record
            return fb_record(component, operation, **base)
        try:
            return record_fn(component, operation, **extended)
        except TypeError:
            return record_fn(component, operation, **base)


class _GatewayFailureMarker:
    __slots__ = ("ref",)

    def __init__(self, ref):
        self.ref = ref


def _chain_inner_diagnostic(exc):
    if exc is None:
        return None
    seen: set[int] = set()
    pending = [exc]
    while pending and len(seen) < 32:
        cur = pending.pop(0)
        if cur is None or id(cur) in seen:
            continue
        seen.add(id(cur))
        marker = getattr(cur, "_mindie_gateway_diag", None)
        if type(marker) is _GatewayFailureMarker and isinstance(marker.ref, dict):
            return dict(marker.ref)
        found = reference({"diagnostic": getattr(cur, "mindie_diagnostic", None)})
        if found is not None:
            return found
        if getattr(cur, "__cause__", None) is not None:
            pending.append(cur.__cause__)
        if getattr(cur, "__context__", None) is not None and not getattr(cur, "__suppress_context__", False):
            pending.append(cur.__context__)
    return None


def failure(operation, stage, category, *, exception=None, revision=None,
            elapsed_ms=None, exit_code=None, reportable=True, incident_id=None,
            reason_code=None, substage=None):
    """Only static product metadata; a trusted inner incident is not recorded twice."""
    trusted = _hex(incident_id, 32)
    if trusted:
        return {"incident_id": trusted, "logging_failed": False}
    inner = _chain_inner_diagnostic(exception)
    if inner is not None:
        return inner
    explicit = _hex(revision, 40)
    eff_reason = reason_code if reason_code is not None else getattr(exception, "reason_code", None)
    eff_substage = substage if substage is not None else getattr(exception, "substage", None)
    try:
        child_build = _project_build(getattr(exception, "mindie_failure_build", None))
        build = child_build or build_metadata()
        raw = _invoke_record(
            _record(), COMPONENT, operation, stage=stage, category=category,
            exception=exception, revision=explicit or build.get("revision"),
            version=build.get("version"), elapsed_ms=elapsed_ms,
            exit_code=exit_code, reportable=reportable,
            source_root=Path(__file__).resolve().parent,
            evidence=getattr(exception, "mindie_failure_evidence", None),
            reason_code=eff_reason, substage=eff_substage,
        )
        if not isinstance(raw, dict):
            raise TypeError("invalid diagnostic result")
        if type(raw.get("recorded")) is not bool or type(raw.get("logging_failed")) is not bool:
            raise TypeError("invalid diagnostic result")
        result = {"recorded": raw["recorded"], "logging_failed": raw["logging_failed"]}
        incident = _hex(raw.get("incident_id"), 32)
        if incident:
            result["incident_id"] = incident
    except TimeoutError:
        _warn()
        result = {"logging_failed": True, "error_type": "TimeoutError"}
    except Exception:
        _warn()
        result = {"logging_failed": True}
    if exception is not None:
        proj = _ref(result)
        if proj is not None:
            try:
                exception._mindie_gateway_diag = _GatewayFailureMarker(proj)
            except Exception:
                pass
    return result


_COMPONENT_PACKAGES = {
    "profiler": ("mindie-profiler", "mindie_profiler"), "sim": ("mindie-sim", "mindie_sim"),
    "coordinator": ("mindie-coordinator", "mindie_coordinator"), "debugger": ("mindie-debugger", "mindie_debugger"),
    "npu_top": ("npu-top", "npu_top"),
}
_REPORTABLE_EXC_TYPES = {
    cls.__name__: cls for cls in (
        RuntimeError, KeyError, AttributeError, IndexError, AssertionError, ZeroDivisionError,
        ImportError, ModuleNotFoundError, NotImplementedError, RecursionError, UnboundLocalError,
        NameError, LookupError, ArithmeticError,
    )
}
_TB_FILE_RE = re.compile(r'^\s*File "([^"]+)", line (\d+), in ([A-Za-z_][A-Za-z0-9_]{0,99}|<module>|<lambda>|<listcomp>|<dictcomp>|<setcomp>|<genexpr>)\s*$')
_TB_EXC_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]{0,119})(?::(?:\s.*|$)|\s*$)")
_STDERR_INCIDENT_RE = re.compile(r"(?:^|\n)mindie-diagnostic-incident:\s*([0-9a-f]{32})\s*(?:$|\r?\n)")
_KNOWN_COMPONENT_STAGES = {
    "profiler": frozenset({"profile", "analyze", "compare", "export", "inspect", "summary", "timeline", "doctor", "version"}),
    "sim": frozenset({"solve", "calibrate", "sweep", "roofline", "predict", "inspect", "catalog", "doctor", "version"}),
    "coordinator": frozenset({"hosts", "run", "exec", "status", "logs", "cancel", "stop", "probe", "doctor", "version"}),
    "debugger": frozenset({"precision", "hang", "oom", "accuracy", "triage", "capsule", "inspect", "doctor", "version"}),
    "npu_top": frozenset({"snapshot", "watch", "top", "serve", "daemon", "status", "doctor", "version"}),
}
_PKG_TO_COMP = {pkg: comp for comp, pkg in _COMPONENT_PACKAGES.values()}
_CHAIN_SEPARATORS = frozenset({
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
})
_DIST_VER_RE = re.compile(r"(?m)^Version:\s*([0-9A-Za-z][0-9A-Za-z.+-]{0,79})\s*$")
_PYPROJ_VER_RE = re.compile(r'(?m)^version\s*=\s*"([0-9A-Za-z][0-9A-Za-z.+-]{0,79})"\s*$')


def _close_fd(fd: int) -> None:
    if fd >= 0:
        try:
            os.close(fd)
        except TimeoutError:
            raise
        except OSError:
            pass


def _read_bytes_bounded(path: Path, limit: int = 16384, *, head_only: bool = False, tail_only: bool = False) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = -1
    try:
        fd = os.open(str(path), flags)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (not head_only and not tail_only and st.st_size > limit):
            return None
        if tail_only and st.st_size > limit:
            os.lseek(fd, st.st_size - limit, os.SEEK_SET)
        rem, chunks = (limit if (head_only or tail_only) else limit + 1), []
        while rem > 0 and (buf := os.read(fd, min(rem, 65536))):
            chunks.append(buf)
            rem -= len(buf)
        raw = b"".join(chunks)
        return raw if (head_only or tail_only or len(raw) <= limit) else None
    except TimeoutError:
        raise
    except OSError:
        return None
    finally:
        _close_fd(fd)


def _git_head_revision(repo_dir: Path) -> str | None:
    git_dir = common_dir = dot_git = repo_dir / ".git"
    if dot_git.is_file():
        if not (raw_ptr := _read_bytes_bounded(dot_git, 512)):
            return None
        ptr = raw_ptr.decode("utf-8", errors="replace").strip()
        if not ptr.startswith("gitdir:"):
            return None
        target = Path(ptr[7:].strip())
        git_dir = target if target.is_absolute() else (repo_dir / target).resolve()
        raw_common = _read_bytes_bounded(git_dir / "commondir", 256)
        rel_c = raw_common.decode("utf-8", errors="replace").strip() if raw_common else ""
        common_dir = (git_dir / rel_c).resolve() if rel_c else git_dir
    if not (raw_head := _read_bytes_bounded(git_dir / "HEAD", 256)):
        return None
    head = raw_head.decode("utf-8", errors="replace").strip()
    if direct := _hex(head, 40):
        return direct
    if head.startswith("ref: ") and (ref_rel := head[5:].strip()).startswith("refs/") and ".." not in ref_rel:
        for base in (git_dir, common_dir):
            if (raw_ref := _read_bytes_bounded(base / ref_rel, 256)) and (rev := _hex(raw_ref.decode("utf-8", errors="replace").strip(), 40)):
                return rev
        if raw_packed := _read_bytes_bounded(common_dir / "packed-refs", 65536):
            for line in raw_packed.decode("utf-8", errors="replace").splitlines():
                if line.endswith(" " + ref_rel) and (rev := _hex(line[:40], 40)):
                    return rev
    return None


def _scan_subdirs_bounded(parent: Path, *, prefix: str = "", suffix: str = "") -> list[Path]:
    matches: list[Path] = []
    try:
        with os.scandir(parent) as it:
            for idx, entry in enumerate(it):
                if idx >= 512 or len(matches) >= 4:
                    break
                if (not prefix or entry.name.startswith(prefix)) and (not suffix or entry.name.endswith(suffix)):
                    if entry.is_dir(follow_symlinks=False):
                        matches.append(Path(entry.path))
    except TimeoutError:
        raise
    except OSError:
        return []
    return sorted(matches)


def _file_url_to_path(url: str) -> Path | None:
    if not isinstance(url, str) or not url.startswith("file://"):
        return None
    from urllib.parse import unquote, urlparse
    raw = unquote(urlparse(url).path or "")
    return Path(raw[1:] if re.match(r"^/[A-Za-z]:/", raw) else raw) if raw else None


def _inspect_dist_info_full(site_dir: Path, dist_norm: str) -> tuple[str | None, str | None, Path | None]:
    for dist_dir in _scan_subdirs_bounded(site_dir, prefix=f"{dist_norm}-", suffix=".dist-info"):
        ver = rev = editable_co = None
        if raw_meta := _read_bytes_bounded(dist_dir / "METADATA", 32768):
            m = _DIST_VER_RE.search(raw_meta.decode("utf-8", errors="replace"))
            ver = m.group(1) if m else None
        if raw_du := _read_bytes_bounded(dist_dir / "direct_url.json", 8192):
            try:
                du = json.loads(raw_du.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                du = {}
            if isinstance(du, dict):
                vcs, dir_info = du.get("vcs_info"), du.get("dir_info")
                rev = _hex(vcs.get("commit_id"), 40) if isinstance(vcs, dict) else None
                if isinstance(dir_info, dict) and dir_info.get("editable") is True and isinstance(du.get("url"), str):
                    editable_co = _file_url_to_path(du["url"])
                    rev = rev or (_git_head_revision(editable_co) if editable_co is not None else None)
        if ver or rev or editable_co is not None:
            return ver, rev, editable_co
    return None, None, None


def _inspect_dist_info(site_dir: Path, dist_norm: str) -> tuple[str | None, str | None]:
    return _inspect_dist_info_full(site_dir, dist_norm)[:2]


def _inspect_checkout(repo_dir: Path, comp_name: str) -> tuple[str | None, str | None]:
    if not (raw_pp := _read_bytes_bounded(repo_dir / "pyproject.toml", 32768)):
        return None, None
    text = raw_pp.decode("utf-8", errors="replace")
    if f'name = "{comp_name}"' not in text and f"name = '{comp_name}'" not in text:
        return None, None
    m = _PYPROJ_VER_RE.search(text)
    return (m.group(1) if m else None), _git_head_revision(repo_dir)


def _venv_roots_from_executable(executable) -> list[Path]:
    if not isinstance(executable, (str, os.PathLike)) or not str(executable):
        return []
    try:
        exe_abs = Path(os.path.abspath(str(Path(executable).expanduser())))
    except TimeoutError:
        raise
    except OSError:
        return []
    roots = [exe_abs.parent.parent] if exe_abs.parent.name.lower() in {"bin", "scripts"} else []
    if (head := _read_bytes_bounded(exe_abs, 256, head_only=True)) and head.startswith(b"#!"):
        line = head.splitlines()[0][2:].decode("utf-8", errors="replace").strip()
        line = line[13:].strip() if line.startswith("/usr/bin/env ") else line
        tok = line.strip("\"'").split()[0] if line else ""
        if tok and os.path.isabs(tok):
            try:
                sb = Path(os.path.abspath(tok))
                if sb.parent.name.lower() in {"bin", "scripts"} and sb.parent.parent not in roots:
                    roots.append(sb.parent.parent)
            except TimeoutError:
                raise
            except OSError:
                pass
    try:
        res = exe_abs.resolve()
        if res.parent.name.lower() in {"bin", "scripts"} and res.parent.parent not in roots:
            roots.append(res.parent.parent)
    except TimeoutError:
        raise
    except OSError:
        pass
    return roots


def _venv_site_dirs(venv_root: Path) -> list[Path]:
    return [venv_root / "Lib" / "site-packages", *[d / "site-packages" for d in _scan_subdirs_bounded(venv_root / "lib", prefix="python")]]


def _trusted_package_dirs(comp_name: str, pkg_prefix: str, executable=None) -> list[Path]:
    dist_norm, trusted = comp_name.lower().replace("-", "_"), []
    for venv_root in _venv_roots_from_executable(executable if executable is not None else sys.executable):
        cands: list[Path] = []
        for sp in _venv_site_dirs(venv_root):
            cands.append(sp / pkg_prefix)
            if (eco := _inspect_dist_info_full(sp, dist_norm)[2]) is not None:
                cands.extend((eco / pkg_prefix, eco / "src" / pkg_prefix))
        if _inspect_checkout(venv_root, comp_name) != (None, None):
            cands.extend((venv_root / pkg_prefix, venv_root / "src" / pkg_prefix))
        for cand in cands:
            try:
                if cand.is_dir() and (rp := cand.resolve()) not in trusted:
                    trusted.append(rp)
            except TimeoutError:
                raise
            except OSError:
                pass
    return trusted


def _resolve_child_component_build(comp_name: str, pkg_roots: list[str], executable=None) -> tuple[str | None, str | None]:
    dist_norm = comp_name.lower().replace("-", "_")
    for raw_root in pkg_roots[:4]:
        rp = Path(raw_root)
        d_ver, d_rev = _inspect_dist_info(rp, dist_norm)
        if d_ver or d_rev:
            return d_ver, d_rev
        if rp.name != "site-packages" and (rp / ".git").exists():
            c_ver, c_rev = _inspect_checkout(rp, comp_name)
            if c_ver or c_rev:
                return c_ver, c_rev
    for venv_root in _venv_roots_from_executable(executable):
        for sp in _venv_site_dirs(venv_root):
            d_ver, d_rev = _inspect_dist_info(sp, dist_norm)
            if d_ver or d_rev:
                return d_ver, d_rev
    return None, None


_FATAL_SIGNAL_REASONS = {
    code: reason
    for sig_name, reason in (
        ("SIGILL", "component_sigill"), ("SIGABRT", "component_sigabrt"),
        ("SIGBUS", "component_sigbus"), ("SIGFPE", "component_sigfpe"),
        ("SIGSEGV", "component_sigsegv"),
    )
    if (sig_num := getattr(signal, sig_name, None)) is not None
    for code in (-int(sig_num), 128 + int(sig_num))
}


def _verified_failure_ids(comp_name: str, *, invocation_token: str | None = None, config=None) -> list[str]:
    tok = _hex(invocation_token, 32)
    if not tok:
        return []
    if callable(cfg_fn := globals().get("diagnostics_config_path")):
        try:
            if config is not None:
                os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(cfg_fn(config))
            else:
                os.environ.setdefault("MINDIE_DIAGNOSTICS_CONFIG", str(cfg_fn(None)))
        except TimeoutError:
            raise
        except Exception:
            pass
    if callable(binder := globals().get("bind_environment")):
        try:
            binder()
        except TimeoutError:
            raise
        except Exception:
            pass
    try:
        from mindie_diagnostics.fallback import default_root
        ev_dir = Path(default_root()) / "events" / comp_name
    except TimeoutError:
        raise
    except Exception:
        if not (raw_root := os.environ.get("MINDIE_DIAGNOSTICS_ROOT")):
            return []
        ev_dir = Path(raw_root).expanduser() / "events" / comp_name
    cands: list[tuple[int, Path]] = []
    try:
        with os.scandir(ev_dir) as it:
            for idx, entry in enumerate(it):
                if idx >= 64:
                    break
                if entry.name.endswith(".jsonl") and entry.is_file(follow_symlinks=False):
                    cands.append((entry.stat(follow_symlinks=False).st_mtime_ns, Path(entry.path)))
    except TimeoutError:
        raise
    except OSError:
        return []
    found: list[str] = []
    for _, fp in sorted(cands, reverse=True)[:8]:
        raw = _read_bytes_bounded(fp, 65536, tail_only=True)
        if not raw:
            continue
        for raw_ln in reversed(raw.splitlines()):
            ln = raw_ln.strip()
            if not (ln.startswith(b"{") and ln.endswith(b"}")):
                continue
            try:
                obj = json.loads(ln.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if (
                isinstance(obj, dict)
                and obj.get("component") == comp_name
                and obj.get("event") == "operation.end"
                and obj.get("status") == "error"
                and obj.get("reportable") is True
                and obj.get("invocation_token") == tok
                and (oid := _hex(obj.get("operation_id"), 32))
                and oid not in found
            ):
                found.append(oid)
    return found


def _verified_local_incident(
    comp_name: str, inc_id: str, *, invocation_token: str | None = None, config=None
) -> bool:
    inc = _hex(inc_id, 32)
    if not inc:
        return False
    return inc in _verified_failure_ids(comp_name, invocation_token=invocation_token, config=config)


def record_component_crash(
    tool_name, argv, exit_code, stderr, *, parsed_result=None, executable=None, invocation_token=None
):
    """Return a provenance-verified child crash receipt or fatal native signal crash envelope."""
    target = _COMPONENT_PACKAGES.get(tool_name)
    if target is None or exit_code in (0, 2, None):
        return None
    comp_name, _pkg_prefix = target
    verified = _verified_failure_ids(comp_name, invocation_token=invocation_token)
    if verified:
        parsed_ref = reference(parsed_result) if isinstance(parsed_result, dict) else None
        parsed_inc = parsed_ref.get("incident_id") if isinstance(parsed_ref, dict) else None
        if parsed_inc in verified:
            return {"incident_id": parsed_inc, "logging_failed": False}
        if isinstance(stderr, str):
            for inc_id in reversed(_STDERR_INCIDENT_RE.findall(stderr)[-4:]):
                if inc_id in verified:
                    return {"incident_id": inc_id, "logging_failed": False}
        return {"incident_id": verified[0], "logging_failed": False}
    sig_reason = _FATAL_SIGNAL_REASONS.get(exit_code) if type(exit_code) is int else None
    if sig_reason is None:
        return None
    allowed_stages = _KNOWN_COMPONENT_STAGES.get(tool_name, frozenset())
    first_arg = argv[0] if isinstance(argv, list) and argv and isinstance(argv[0], str) else None
    stage = first_arg if first_arg in allowed_stages else "cli"
    c_ver, c_rev = _resolve_child_component_build(comp_name, [], executable=executable)
    try:
        raw = _invoke_record(
            _record(), comp_name, "cli.execute", stage=stage, category="subprocess", exception=None,
            revision=c_rev, version=c_ver, elapsed_ms=None, exit_code=exit_code, reportable=True,
            source_root=None, evidence=None, reason_code=sig_reason, substage=None,
            infer_build_meta=False,
        )
        inc = _hex(raw.get("incident_id"), 32) if isinstance(raw, dict) else None
        return {"incident_id": inc, "logging_failed": False} if inc else {"logging_failed": True}
    except TimeoutError:
        raise
    except Exception:
        return {"logging_failed": True}


def _ref(diagnostic):
    if not isinstance(diagnostic, dict):
        return None
    failed = diagnostic.get("logging_failed", False)
    if type(failed) is not bool:
        return None
    incident = _hex(diagnostic.get("incident_id"), 32)
    if incident:
        return {"incident_id": incident, "logging_failed": failed}
    if failed is True:
        return {"logging_failed": True}
    return None


def reference(result):
    """Inspect only structured references returned by a trusted inner boundary."""
    if not isinstance(result, dict):
        return None
    found = _ref(result.get("diagnostic"))
    if found is not None:
        return found
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return _ref(structured.get("diagnostic"))
    return None


def attach(result, diagnostic):
    """Preserve result semantics; expose only the projected incident reference."""
    if not isinstance(result, dict) or reference(result) is not None:
        return result
    projected = _ref(diagnostic)
    if projected is None:
        return result
    result = dict(result, diagnostic=projected)
    incident = projected.get("incident_id")
    text = (f"MindIE incident {incident}; read-only local status: /mindie-agent:reporting-status. Fault uploads follow the contribution switch."
            if incident else _STORAGE)
    content = result.get("content")
    if isinstance(content, list):
        result["content"] = [*content, {"type": "text", "text": text}]
    elif "content" not in result:
        result["content"] = [{"type": "text", "text": text}]
    return result


def reporting_config_path(config_file=None):
    explicit = os.environ.get("MINDIE_DIAGNOSTICS_CONFIG")
    if explicit:
        return Path(explicit).expanduser().absolute()
    from paths import base_config_path, config_path
    profile = Path(config_file) if config_file is not None else base_config_path()
    adapter_path = Path(config_file) if config_file is not None else config_path()
    if adapter_path.is_file():
        adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
        selected = adapter.get("diagnostics_context", {}).get("MINDIE_DIAGNOSTICS_CONFIG")
        if selected:
            return Path(selected).expanduser().absolute()
    return profile.expanduser().absolute().parent / "diagnostics.json"


def _unavailable(stage, error_type):
    return {
        "enabled": None, "status": "unavailable",
        "error": {"stage": stage, "type": error_type},
        "hint": "Inspect the selected MindIE runtime; no repair or retry was started.",
    }


def reporting_status():
    try:
        from mindie_diagnostics.integration import reporting_status as upstream
        import consent
        result = upstream(config=reporting_config_path())
        return effective_reporting(result, consent.load())
    except Exception as exc:
        return _unavailable("reporting_status", type(exc).__name__)


def activate_reporting(config_file=None, *, ensure=True):
    """Derive policy from the existing authority; only long-lived callers ensure."""
    from mindie_knowledge.consent_store import activate_reporting as activate
    import consent
    policy = reporting_config_path(config_file)
    authority = consent.consent_path(config_file)
    return activate(authority, config=policy, repository=REPOSITORY, ensure=ensure)


def activation_handoff(python):
    """The native ten-second Hook configures; the same Skill finishes outside it."""
    from paths import base_config_path
    result = activate_reporting(ensure=False)
    if result.get("status") == "configured" and result.get("enabled"):
        command = [python, str(Path(__file__).resolve()), "activate",
                   "--diagnostics-config", str(reporting_config_path()),
                   "--config", str(base_config_path())]
        if os.environ.get("MINDIE_DIAGNOSTICS_ROOT"):
            command.extend(["--diagnostics-root", os.environ["MINDIE_DIAGNOSTICS_ROOT"]])
        result = dict(result, status="reporter_pending", run_outside_hook=True,
                      command=command, command_line=ensure_command_line(command),
                      command_shell="powershell" if os.name == "nt" else "posix",
                      note="Execute this command now in the same contribution setup flow; verify worker readiness before reporting completion.")
    return result


def ensure_command_line(command):
    if os.name == "nt":
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in command)
    return shlex.join(command)


def effective_reporting(view, saved):
    """The authority gates status too; a legacy choice cannot widen consent."""
    if saved.get("state") in {"corrupt", "unreadable"}:
        return dict(view, enabled=False, status="unavailable", consent=saved)
    if not saved.get("choice_revision"):
        return dict(view, enabled=False, status="needs_choice", consent=saved,
                    detail="The saved choice does not authorize sanitized fault reporting.")
    if saved.get("choice") != "contribute":
        return dict(view, enabled=False, status="disabled", consent=saved)
    return dict(view, consent=saved)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Activate fault reporting from the saved contribution choice.")
    parser.add_argument("operation", choices=["activate", "status"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--diagnostics-config", type=Path)
    parser.add_argument("--diagnostics-root", type=Path)
    args = parser.parse_args(argv)
    os.environ["MINDIE_CC_CONFIG"] = str(args.config.expanduser().absolute())
    if args.diagnostics_config:
        os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(args.diagnostics_config.expanduser().absolute())
    if args.diagnostics_root:
        os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(args.diagnostics_root.expanduser().absolute())
    try:
        result = reporting_status() if args.operation == "status" else activate_reporting()
    except Exception as exc:
        result = _unavailable("reporting_activation", type(exc).__name__)
    print(json.dumps(result))
    return int(result.get("status") not in {"running", "disabled"}
               if args.operation == "activate" else result.get("status") == "unavailable")


if __name__ == "__main__":
    raise SystemExit(main())
