"""The stamp exists to answer "am I running the code I think I am?".

A stamp that is wrong is worse than no stamp, because it is believed. These
check the two ways it can lie: a ref it cannot resolve, and a fingerprint that
does not move when the contents do.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from intake import buildinfo
from intake.buildinfo import Stamp

SHA = "3ea7db9dbd4b27fb512a3e04155cf90e41dadff6"


def git_dir(tmp_path: Path, head: str, refs: dict[str, str] | None = None,
            packed: str | None = None) -> Path:
    root = tmp_path / ".git"
    root.mkdir(parents=True, exist_ok=True)
    (root / "HEAD").write_text(head, encoding="utf-8")
    for name, sha in (refs or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sha + "\n", encoding="utf-8")
    if packed is not None:
        (root / "packed-refs").write_text(packed, encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# Resolving the commit
# --------------------------------------------------------------------------- #


def test_a_loose_ref_resolves(tmp_path: Path) -> None:
    where = git_dir(tmp_path, "ref: refs/heads/main\n", {"refs/heads/main": SHA})
    assert buildinfo.resolve(where) == (SHA[:7], "main")


def test_a_branch_name_with_slashes_resolves(tmp_path: Path) -> None:
    """The branch this project is developed on is `claude/inspiring-mendel-mdxt7i`.
    Splitting a ref on "/" instead of stripping one prefix loses it."""
    ref = "refs/heads/claude/inspiring-mendel-mdxt7i"
    where = git_dir(tmp_path, f"ref: {ref}\n", {ref: SHA})
    assert buildinfo.resolve(where) == (SHA[:7], "claude/inspiring-mendel-mdxt7i")


def test_a_packed_ref_resolves(tmp_path: Path) -> None:
    """`git gc` moves refs out of refs/heads/ into packed-refs, and it runs on its
    own schedule. Reading only loose refs works until the day it does."""
    where = git_dir(
        tmp_path,
        "ref: refs/heads/main\n",
        packed=f"# pack-refs with: peeled fully-peeled sorted\n{SHA} refs/heads/main\n",
    )
    assert buildinfo.resolve(where) == (SHA[:7], "main")


def test_an_annotated_tag_line_is_not_mistaken_for_the_ref(tmp_path: Path) -> None:
    """packed-refs follows a tag with a "^" line holding the commit it points at.
    Reading that as a ref would attribute the wrong sha to the branch."""
    packed = (
        f"{'a' * 40} refs/tags/v1\n"
        f"^{'b' * 40}\n"
        f"{SHA} refs/heads/main\n"
    )
    where = git_dir(tmp_path, "ref: refs/heads/main\n", packed=packed)
    assert buildinfo.resolve(where) == (SHA[:7], "main")


def test_a_detached_head_reports_the_commit_and_no_branch(tmp_path: Path) -> None:
    where = git_dir(tmp_path, SHA + "\n")
    assert buildinfo.resolve(where) == (SHA[:7], "")


def test_an_unresolvable_ref_says_nothing_rather_than_guessing(tmp_path: Path) -> None:
    where = git_dir(tmp_path, "ref: refs/heads/gone\n")
    assert buildinfo.resolve(where) == ("", "")


def test_a_missing_git_directory_is_not_an_error(tmp_path: Path) -> None:
    assert buildinfo.resolve(tmp_path / "nope") == ("", "")


def test_it_agrees_with_git_on_this_repository() -> None:
    """The point of the whole module. A resolver that disagrees with `git log` is
    worse than useless, because the banner is what someone will believe."""
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        import pytest

        pytest.skip("not a git checkout")

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()

    commit, branch = buildinfo.resolve(root / ".git")
    assert commit == git("rev-parse", "--short=7", "HEAD")

    # git spells a detached HEAD "HEAD"; this module spells it "".
    named = git("rev-parse", "--abbrev-ref", "HEAD")
    assert branch == ("" if named == "HEAD" else named)


# --------------------------------------------------------------------------- #
# The fingerprint
# --------------------------------------------------------------------------- #


def test_the_fingerprint_moves_when_a_file_changes(tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    target = tmp_path / "backend" / "thing.py"
    target.write_text("one", encoding="utf-8")
    before = buildinfo.fingerprint(tmp_path)
    target.write_text("two", encoding="utf-8")
    assert buildinfo.fingerprint(tmp_path) != before


def test_the_fingerprint_moves_when_a_file_is_renamed(tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "a.py").write_text("same", encoding="utf-8")
    before = buildinfo.fingerprint(tmp_path)
    (tmp_path / "backend" / "a.py").rename(tmp_path / "backend" / "b.py")
    assert buildinfo.fingerprint(tmp_path) != before


def test_the_fingerprint_ignores_bytecode(tmp_path: Path) -> None:
    """Bytecode embeds source mtimes, which differ between checkouts of identical
    content. Hashing it would make the fingerprint vary for no reason a reader
    could see."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "thing.py").write_text("one", encoding="utf-8")
    before = buildinfo.fingerprint(tmp_path)
    cache = tmp_path / "backend" / "__pycache__"
    cache.mkdir()
    (cache / "thing.pyc").write_bytes(b"\x00\x01")
    assert buildinfo.fingerprint(tmp_path) == before


def test_the_fingerprint_is_stable_for_identical_contents(tmp_path: Path) -> None:
    for name in ("one", "two"):
        (tmp_path / name / "backend").mkdir(parents=True)
        (tmp_path / name / "backend" / "x.py").write_text("same", encoding="utf-8")
    assert buildinfo.fingerprint(tmp_path / "one") == buildinfo.fingerprint(tmp_path / "two")


# --------------------------------------------------------------------------- #
# Reading it back
# --------------------------------------------------------------------------- #


def test_a_written_stamp_reads_back(tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "x.py").write_text("x", encoding="utf-8")
    where = git_dir(tmp_path, "ref: refs/heads/main\n", {"refs/heads/main": SHA})
    written = buildinfo.write(tmp_path / buildinfo.STAMP_NAME, tmp_path, where)
    assert buildinfo.read(tmp_path) == written
    assert written.build and written.commit == SHA[:7]


def test_the_commit_survives_the_handoff_between_build_stages(tmp_path: Path) -> None:
    """The commit is resolved in a stage that has .git; the fingerprint in the
    stage that has the built contents. If the carry between them breaks, the
    banner silently loses the half that is actually actionable."""
    where = git_dir(tmp_path, "ref: refs/heads/main\n", {"refs/heads/main": SHA})

    # Stage one: the commit, no tree to fingerprint.
    carried = tmp_path / "SOURCE"
    first = buildinfo.write(carried, git_dir=where)
    assert first.commit == SHA[:7] and first.build == ""

    # Stage two: the fingerprint, no .git in sight.
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "x.py").write_text("x", encoding="utf-8")
    final = buildinfo.write(tmp_path / buildinfo.STAMP_NAME, tmp_path, source=carried)
    assert final.commit == SHA[:7]
    assert final.branch == "main"
    assert final.build


def test_a_checkout_names_its_commit_but_claims_no_image(tmp_path: Path) -> None:
    """`make api` runs from a checkout. It should still say which commit it is on,
    and must not report a build fingerprint, because nothing was built."""
    git_dir(tmp_path, "ref: refs/heads/main\n", {"refs/heads/main": SHA})
    stamp = buildinfo.read(tmp_path)
    assert stamp.commit == SHA[:7]
    assert stamp.build == ""
    assert "source checkout" in stamp.describe()


def test_an_unstamped_directory_does_not_invent_anything(tmp_path: Path) -> None:
    assert buildinfo.read(tmp_path) == Stamp()


def test_a_corrupt_stamp_falls_back_instead_of_crashing_startup(tmp_path: Path) -> None:
    (tmp_path / buildinfo.STAMP_NAME).write_text("{not json", encoding="utf-8")
    assert buildinfo.read(tmp_path) == Stamp()


def test_the_description_names_both_halves() -> None:
    line = Stamp(build="9a8ef148270c", commit="3ea7db9", branch="main").describe()
    assert "3ea7db9" in line and "main" in line and "9a8ef148270c" in line
