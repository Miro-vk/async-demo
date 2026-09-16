"""What this image is, and which source it was built from.

Two different questions get asked when a change does not show up, and they need
two different answers:

  "Did the image rebuild?"     -- the content fingerprint answers this
  "Did it rebuild *my* code?"  -- only the commit answers this

The second one is the actionable half. A fingerprint is unfalsifiable but
incomparable: seeing the same twelve hex digits twice tells you nothing about
what you should have had instead. A commit can be held up against `git log` and
settles it in one look.

Both are resolved during the image build and written to a file, because the
running container has neither git nor a network. The ref is read out of .git by
hand rather than by shelling out, so the runtime image does not carry a git
install for the sake of one string produced at build time.

Whether that commit is the *latest* one is a question only the host can answer --
it needs a fetch. `make demo` asks it there and says so before building.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

# What the fingerprint covers: the code, the built front end, and the corpus and
# decisions baked in beside them. Everything a change to this demo can land in.
FINGERPRINTED = ("backend", "frontend/dist", "data")

STAMP_NAME = "BUILD_STAMP"

# Bytecode is a build artefact of the build itself, and its embedded source
# mtimes vary between checkouts of identical content.
_SKIP_DIRS = {"__pycache__", ".pytest_cache"}


@dataclass(frozen=True)
class Stamp:
    """Where this process's code came from. Every field may be empty."""

    build: str = ""
    """Fingerprint of the image contents. Empty when running from a checkout."""

    commit: str = ""
    branch: str = ""

    def describe(self) -> str:
        """One line for the startup banner and the health endpoint."""
        if self.commit:
            where = f" ({self.branch})" if self.branch else " (detached)"
            source = f"built from {self.commit}{where}"
        else:
            source = "built from an unknown revision"
        return f"{source} · image {self.build}" if self.build else f"{source} · source checkout"


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _files(root: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for part in FINGERPRINTED:
        base = root / part
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if _SKIP_DIRS & set(path.relative_to(root).parts):
                continue
            found.append((path.relative_to(root).as_posix(), path))
    return sorted(found)


def fingerprint(root: Path) -> str:
    """A short hash of everything that goes into the image.

    Names are hashed alongside contents so that moving a file changes the result;
    a rename with no edit is still a different build.
    """
    sha = hashlib.sha256()
    for name, path in _files(root):
        sha.update(name.encode("utf-8"))
        sha.update(b"\0")
        sha.update(_digest(path).encode("ascii"))
    return sha.hexdigest()[:12]


def _packed_ref(git_dir: Path, ref: str) -> str:
    """Look a ref up in packed-refs, where git puts it after `git gc`."""
    packed = git_dir / "packed-refs"
    try:
        lines = packed.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        if line.startswith(("#", "^")):
            continue
        sha, _, name = line.partition(" ")
        if name.strip() == ref:
            return sha.strip()
    return ""


def resolve(git_dir: Path) -> tuple[str, str]:
    """(short commit, branch) from a .git directory. ("", "") if unreadable.

    Only HEAD, refs/ and packed-refs are needed, which is why .dockerignore lets
    exactly those through and nothing else.
    """
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return "", ""

    if not head.startswith("ref:"):
        # Detached HEAD: the file holds the commit itself.
        return head[:7], ""

    ref = head.removeprefix("ref:").strip()
    try:
        sha = (git_dir / ref).read_text(encoding="utf-8").strip()
    except OSError:
        sha = _packed_ref(git_dir, ref)
    if not sha:
        return "", ""
    return sha[:7], ref.removeprefix("refs/heads/")


def write(out: Path, root: Path | None = None, git_dir: Path | None = None,
          source: Path | None = None) -> Stamp:
    """Write a stamp from whichever halves are available.

    The two halves are resolved in different places: the commit in a throwaway
    stage that has .git, the fingerprint in the final stage that has the built
    contents. `source` carries the first across to the second.
    """
    if source is not None:
        carried = read_file(source)
        commit, branch = carried.commit, carried.branch
    elif git_dir is not None:
        commit, branch = resolve(git_dir)
    else:
        commit, branch = "", ""

    stamp = Stamp(
        build=fingerprint(root) if root is not None else "",
        commit=commit,
        branch=branch,
    )
    out.write_text(json.dumps(stamp.__dict__, indent=2) + "\n", encoding="utf-8")
    return stamp


def read_file(path: Path) -> Stamp:
    """One stamp file, or an empty stamp if it is missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Stamp()
    return Stamp(
        build=str(data.get("build", "")),
        commit=str(data.get("commit", "")),
        branch=str(data.get("branch", "")),
    )


def read(root: Path) -> Stamp:
    """The stamp written at image build time, or the best the checkout can offer.

    A checkout has no stamp but does have .git, so `make api` still names its
    commit. It never claims a build fingerprint, because nothing was built.
    """
    stamp = read_file(root / STAMP_NAME)
    if stamp != Stamp():
        return stamp
    commit, branch = resolve(root / ".git")
    return Stamp(commit=commit, branch=branch)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a build stamp.")
    parser.add_argument("--out", type=Path, default=Path(STAMP_NAME))
    parser.add_argument("--root", type=Path, help="fingerprint this tree")
    parser.add_argument("--git-dir", type=Path, help="resolve the commit from here")
    parser.add_argument("--source", type=Path, help="carry the commit from this stamp")
    args = parser.parse_args()
    print(write(args.out, args.root, args.git_dir, args.source).describe())


if __name__ == "__main__":
    main()
