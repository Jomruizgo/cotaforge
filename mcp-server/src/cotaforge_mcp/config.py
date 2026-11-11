"""Server configuration, read from the environment (one process = one adapter + one live document)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cotaforge import paths
from cotaforge.contract import Adapter
from cotaforge.memory import HOME_ENV_VAR, user_memory_dir
from cotaforge.memory.store import USER_MEMORY_DIRNAME

from cotaforge_mcp.library.files import INDEX_DIRNAME, INDEX_FILENAME, LIBRARY_DIRNAME
from cotaforge_mcp.parts.user import USER_CATALOG_DIRNAME

ENV_HOME = HOME_ENV_VAR
ENV_LIBRARY = "COTAFORGE_LIBRARY"
ENV_ADAPTER = "COTAFORGE_ADAPTER"
ENV_WORKSPACE = "COTAFORGE_WORKSPACE"
ENV_FREECAD_ROOT = "COTAFORGE_FREECAD_ROOT"
ENV_VISIBLE = "COTAFORGE_VISIBLE"
ENV_BASE_MEMORY = "COTAFORGE_BASE_MEMORY"
ENV_IMPORT_STALL_S = "COTAFORGE_IMPORT_STALL_S"
ENV_IMPORT_MAX_S = "COTAFORGE_IMPORT_MAX_S"

ADAPTERS = ("freecad", "fake")


class ConfigError(ValueError):
    """The environment asks for something the server cannot do."""


def _seconds(env: Mapping[str, str], name: str) -> float | None:
    """A positive number of seconds from the environment; ``None`` when the variable is not set."""
    text = env.get(name, "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        value = 0.0
    if not 0 < value < float("inf"):
        msg = f"{name}={text!r} is not a positive number of seconds"
        raise ConfigError(msg)
    return value


@dataclass(frozen=True)
class Settings:
    adapter: str = "freecad"
    workspace: Path = Path()
    freecad_root: Path | None = None
    visible: bool = False
    home: Path | None = None
    """``COTAFORGE_HOME``: the folder that replaces ``~/.cotaforge`` (the user scope of the memory)."""
    base_memory: bool = True
    """``COTAFORGE_BASE_MEMORY=0`` leaves out the base set of principles that ships with the product."""
    import_stall_s: float | None = None
    """``COTAFORGE_IMPORT_STALL_S``: seconds an import may go without the CAD process using any CPU before it is
    given up on (the adapter's default when unset)."""
    import_max_s: float | None = None
    """``COTAFORGE_IMPORT_MAX_S``: seconds an import may take in all (the adapter's default when unset)."""
    cad: str | None = None
    """The key of the running adapter (``Adapter.cad_key``), set by the session: not an environment setting."""
    library: Path | None = None
    """``COTAFORGE_LIBRARY``: the technical library folder (e.g. a team network folder), not ``<home>/library``."""

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        adapter = env.get(ENV_ADAPTER, "freecad").strip().lower() or "freecad"
        if adapter not in ADAPTERS:
            msg = f"{ENV_ADAPTER}={adapter!r} is not one of {', '.join(ADAPTERS)}"
            raise ConfigError(msg)
        # absolute, never resolved: resolving would expand a Windows ``subst`` drive (or a symlink) to its long real
        # target, and FreeCAD's temp/backup files next to a document then overrun MAX_PATH
        workspace = Path(os.path.abspath(Path(env.get(ENV_WORKSPACE) or Path.cwd()).expanduser()))
        root = env.get(ENV_FREECAD_ROOT)
        visible = env.get(ENV_VISIBLE, "").strip().lower() in ("1", "true", "yes", "on")
        return cls(
            import_stall_s=_seconds(env, ENV_IMPORT_STALL_S),
            import_max_s=_seconds(env, ENV_IMPORT_MAX_S),
            adapter=adapter,
            workspace=workspace,
            freecad_root=Path(root).expanduser() if root else None,
            visible=visible,
            base_memory=env.get(ENV_BASE_MEMORY, "1").strip().lower() not in ("0", "false", "no", "off"),
            home=Path(env[ENV_HOME]).expanduser() if env.get(ENV_HOME, "").strip() else None,
            library=Path(env[ENV_LIBRARY]).expanduser() if env.get(ENV_LIBRARY, "").strip() else None,
        )

    def user_memory_dir(self) -> Path:
        """The user scope of the memory: ``<home>/memory`` or, without ``COTAFORGE_HOME``, ``~/.cotaforge/memory``."""
        return paths.resolve(self.home, USER_MEMORY_DIRNAME) if self.home is not None else user_memory_dir()

    def user_catalog_dir(self) -> Path:
        """The parts catalog: ``<home>/catalog`` or, without ``COTAFORGE_HOME``, ``~/.cotaforge/catalog``."""
        base = self.home if self.home is not None else Path.home() / ".cotaforge"
        return paths.resolve(base, USER_CATALOG_DIRNAME)

    def library_dir(self) -> Path:
        """The technical library: ``COTAFORGE_LIBRARY``, else ``<home>/library`` (``~/.cotaforge/library``)."""
        if self.library is not None:
            return self.library
        base = self.home if self.home is not None else Path.home() / ".cotaforge"
        return paths.resolve(base, LIBRARY_DIRNAME)

    def library_index_path(self) -> Path:
        """The full-text index of the library, outside it so a shared folder holds only documents."""
        base = self.home if self.home is not None else Path.home() / ".cotaforge"
        return paths.resolve(paths.resolve(base, INDEX_DIRNAME), INDEX_FILENAME)

    def resolve(self, path: str) -> Path:
        """A path given by the agent: relative ones live in the design space (the workspace)."""
        candidate = Path(path).expanduser()
        return candidate if candidate.is_absolute() else self.workspace / candidate


def make_adapter(settings: Settings) -> Adapter:
    """Build the adapter named by the settings (imported lazily: the fake needs no CAD, FreeCAD needs its root)."""
    if settings.adapter == "fake":
        from cotaforge.adapters.fake import FakeAdapter  # noqa: PLC0415

        return FakeAdapter()
    from cotaforge.adapters.freecad import FreeCADAdapter  # noqa: PLC0415

    limits: dict[str, float] = {}
    if settings.import_stall_s is not None:
        limits["import_stall_s"] = settings.import_stall_s
    if settings.import_max_s is not None:
        limits["import_max_s"] = settings.import_max_s
    return FreeCADAdapter(freecad_root=settings.freecad_root, visible=settings.visible, **limits)
