"""Session-isolated, read-only runtime for the public BAEC Console."""

from __future__ import annotations

import shutil
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import mkdtemp

from . import demo_seed
from .console_service import Console, ConsoleError


PUBLIC_ACTOR = "Public demo visitor"

MUTATING_SERVICE_METHODS = frozenset({
    "destroy",
    "set_pref",
    "create_account",
    "record_interaction",
    "capture_condition",
    "confirm_condition",
    "record_not_confirmable",
    "discard_draft",
    "record_dormancy_judgment",
    "change_account_state",
    "start_monitoring",
    "add_observation",
    "submit_correspondence_review",
    "block_preparation",
    "unblock_preparation",
    "request_preparation",
    "generate_preparation",
    "edit_artifact",
    "review_artifact",
})

MUTATING_CONSOLE_METHODS = frozenset({
    "reset_synthetic",
    "set_actor",
})


def _remove_directory(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _blocked(*args, **kwargs):
    raise ConsoleError(
        "This action is locked in the public demo. "
        "The full Console supports it, but public sessions are read-only."
    )


class ReadOnlyService:
    def __init__(self, service) -> None:
        self._service = service

    def __getattr__(self, name):
        if name in MUTATING_SERVICE_METHODS:
            return _blocked
        return getattr(self._service, name)


class ReadOnlyConsole:
    def __init__(self, console: Console) -> None:
        self._console = console
        self.local = ReadOnlyService(console.local)
        self.synthetic = ReadOnlyService(console.synthetic)

    @property
    def services(self):
        return (self.local, self.synthetic)

    @property
    def actor(self) -> str:
        return self._console.actor

    @property
    def root(self):
        return self._console.root

    def owner(self, account_id: str):
        if self.local.has_account(account_id):
            return self.local
        if self.synthetic.has_account(account_id):
            return self.synthetic
        return self._console.owner(account_id)

    def owner_of_preparation(self, preparation_id: str):
        if preparation_id in self.local.preparation_ids():
            return self.local
        if preparation_id in self.synthetic.preparation_ids():
            return self.synthetic
        return self._console.owner_of_preparation(preparation_id)

    def __getattr__(self, name):
        if name in MUTATING_CONSOLE_METHODS:
            return _blocked
        return getattr(self._console, name)


@dataclass
class PublicRuntime:
    directory: Path
    console: ReadOnlyConsole
    account_id: str
    _finalizer: weakref.finalize = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._finalizer = weakref.finalize(
            self,
            _remove_directory,
            self.directory,
        )

    def cleanup(self) -> None:
        if self._finalizer.alive:
            self._finalizer()


def new_public_runtime() -> PublicRuntime:
    directory = Path(mkdtemp(prefix="baec-console-public-"))

    writable_console = Console(directory)
    writable_console.set_actor(PUBLIC_ACTOR)

    account_id = demo_seed.load(
        writable_console,
        "completed",
    )

    return PublicRuntime(
        directory=directory,
        console=ReadOnlyConsole(writable_console),
        account_id=account_id,
    )
