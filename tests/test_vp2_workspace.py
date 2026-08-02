"""VP-2 Project Workspace: пути/allowlist, git baseline, worktree, аренды,
безопасность архивов и traversal (Master Spec §13.4, §30.1, §35).

Работает на РЕАЛЬНОМ синтетическом git-репозитории (не только моки): проверяет
неизменность dirty-состояния байт-в-байт, безопасное создание worktree, отказ
второму writer и блокировку traversal/symlink архивов.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import tarfile
import unittest

from atlas_test_base import AtlasTestCase

# Синтетические идентичности git без TLD — не матчат email-правило редактора.
_GIT_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", "/tmp"),
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "Atlas Test", "GIT_AUTHOR_EMAIL": "atlas@local",
    "GIT_COMMITTER_NAME": "Atlas Test", "GIT_COMMITTER_EMAIL": "atlas@local",
    "LC_ALL": "C",
}


def _git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], check=True,
                          capture_output=True, text=True, env=_GIT_ENV)


def _make_repo(path, *, remote=None, dirty=False, instructions=True):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write("# synthetic\n")
    with open(os.path.join(path, "package.json"), "w") as f:
        f.write('{"name":"syn","scripts":{"build":"vite build","test":"vitest"}}\n')
    if instructions:
        with open(os.path.join(path, "AGENTS.md"), "w") as f:
            f.write("Root instructions: build then test.\n")
        os.makedirs(os.path.join(path, "sub"), exist_ok=True)
        with open(os.path.join(path, "sub", "CLAUDE.md"), "w") as f:
            f.write("Nested instructions for sub/.\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "initial")
    if remote:
        _git(path, "remote", "add", "origin", remote)
    if dirty:
        with open(os.path.join(path, "README.md"), "a") as f:
            f.write("uncommitted tracked change\n")
        with open(os.path.join(path, "NEW_UNTRACKED.txt"), "w") as f:
            f.write("untracked owner work\n")
    return path


def _tree_hash(root):
    """Хеш рабочего дерева без .git (для доказательства неизменности)."""
    acc = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d != ".git"]
        for name in fns:
            fp = os.path.join(dp, name)
            rel = os.path.relpath(fp, root)
            try:
                with open(fp, "rb") as fh:
                    acc[rel] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                acc[rel] = "ERR"
    blob = "\n".join(f"{k}:{acc[k]}" for k in sorted(acc))
    return hashlib.sha256(blob.encode()).hexdigest(), acc


class VP2Base(AtlasTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        from atlas_core.db import get_engine, init_engine
        from atlas_core.orm import Base
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        init_engine(self.settings.db_url, self.settings.db_path)
        Base.metadata.create_all(get_engine())
        from atlas_core.workspace import WorkspaceService
        self.svc = WorkspaceService(self.settings)

    def _repo_under_allowlist(self, name="syn", **kw):
        path = os.path.join(self.svc.roots.projects_root, name)
        return _make_repo(path, **kw)


class TestPathGuard(VP2Base):
    def test_blocks_traversal_and_symlink_escape(self):
        from atlas_core.wspaths import PathTraversalError, WorkspaceGuard
        guard = WorkspaceGuard([self.svc.roots.projects_root])
        # прямой traversal наружу
        with self.assertRaises(PathTraversalError):
            guard.ensure_within(os.path.join(self.svc.roots.projects_root, "..", "escape"))
        # symlink-escape: ссылка внутри allowlist на /etc
        link = os.path.join(self.svc.roots.projects_root, "evil-link")
        os.symlink("/etc", link)
        with self.assertRaises(PathTraversalError):
            guard.ensure_within(os.path.join(link, "passwd"))
        # легальный путь внутри — ок
        ok = os.path.join(self.svc.roots.projects_root, "real")
        os.makedirs(ok)
        self.assertEqual(guard.ensure_within(ok, must_exist=True), os.path.realpath(ok))


class TestArchiveSecurity(VP2Base):
    def _tar_with(self, members):
        buf = os.path.join(self.data_dir, "mal.tar")
        with tarfile.open(buf, "w") as tar:
            for ti, data in members:
                if data is None:
                    tar.addfile(ti)
                else:
                    ti.size = len(data)
                    tar.addfile(ti, io.BytesIO(data))
        return buf

    def test_relpath_rejects_absolute_dotdot_windows(self):
        from atlas_core.wspaths import UnsafeArchiveError, safe_member_relpath
        for bad in ("/etc/passwd", "../escape", "a/../../b", "C:\\win", "foo\\bar", ""):
            with self.assertRaises(UnsafeArchiveError, msg=bad):
                safe_member_relpath(bad)
        self.assertEqual(safe_member_relpath("a/./b/c.txt"), "a/b/c.txt")

    def test_extract_blocks_dotdot_no_file_outside(self):
        from atlas_core.archives import safe_extract
        from atlas_core.wspaths import UnsafeArchiveError
        mal = self._tar_with([
            (tarfile.TarInfo("ok.txt"), b"ok"),
            (tarfile.TarInfo("../../pwned.txt"), b"pwn"),
        ])
        sentinel = os.path.join(self.data_dir, "pwned.txt")
        with self.assertRaises(UnsafeArchiveError):
            safe_extract(mal, self.svc.roots.intake_root, "job1")
        # ни одного файла вне intake
        self.assertFalse(os.path.exists(sentinel))
        self.assertFalse(os.path.exists(os.path.join(self.data_dir, "..", "pwned.txt")))

    def test_extract_blocks_symlink_escape(self):
        from atlas_core.archives import safe_extract
        from atlas_core.wspaths import UnsafeArchiveError
        sym = tarfile.TarInfo("link")
        sym.type = tarfile.SYMTYPE
        sym.linkname = "/etc/passwd"
        mal = self._tar_with([(sym, None)])
        with self.assertRaises(UnsafeArchiveError):
            safe_extract(mal, self.svc.roots.intake_root, "job2")

    def test_extract_clean_archive_readonly(self):
        from atlas_core.archives import safe_extract
        good = self._tar_with([
            (tarfile.TarInfo("dir/a.txt"), b"hello"),
            (tarfile.TarInfo("b.txt"), b"world"),
        ])
        res = safe_extract(good, self.svc.roots.intake_root, "job3")
        a = os.path.join(res["extracted_to"], "dir", "a.txt")
        self.assertTrue(os.path.exists(a))
        self.assertTrue(res["extracted_to"].startswith(self.svc.roots.intake_root))
        # read-only intake (проверяем биты режима: root игнорирует os.access)
        self.assertEqual(os.stat(a).st_mode & 0o222, 0)


class TestGitBaseline(VP2Base):
    def test_clean_baseline_persisted_and_visible(self):
        self._repo_under_allowlist(remote="https://token123@github.com/o/r.git")
        ov = self.svc.connect_project("syn", "local_git",
                                      path=os.path.join(self.svc.roots.projects_root, "syn"))
        bl = ov["baseline"]
        self.assertEqual(bl["branch"], "main")
        self.assertEqual(len(bl["head"]), 40)
        # remote санирован (без credentials)
        self.assertTrue(bl["remotes"])
        self.assertNotIn("token123", bl["remotes"][0]["url"])
        self.assertEqual(bl["remotes"][0]["url"], "https://github.com/o/r.git")
        # инструкции с precedence (root=0, вложенная глубже)
        paths = {i["path"]: i["precedence"] for i in bl["instructions"]}
        self.assertIn("AGENTS.md", paths)
        self.assertIn("sub/CLAUDE.md", paths)
        self.assertGreater(paths["sub/CLAUDE.md"], paths["AGENTS.md"])
        # пакетные менеджеры и команды
        self.assertTrue(any(p["name"] in ("npm", "pnpm") for p in bl["package_managers"]))
        cmds = {c["name"]: c for c in bl["baseline_commands"]}
        self.assertIn("build", cmds)
        self.assertFalse(cmds["build"]["executed"])  # команды не исполняются
        self.assertEqual(ov["state"], "clean")
        self.assertTrue(bl["content_hash"].startswith("sha256:"))

    def test_baseline_survives_restart(self):
        self._repo_under_allowlist()
        ov = self.svc.connect_project("syn", "local_git",
                                      path=os.path.join(self.svc.roots.projects_root, "syn"))
        pid = ov["project"]["id"]
        # «рестарт Core»: заново init_engine на тот же файл БД
        from atlas_core.db import init_engine
        init_engine(self.settings.db_url, self.settings.db_path)
        from atlas_core.workspace import WorkspaceService
        svc2 = WorkspaceService(self.settings)
        ov2 = svc2.overview(pid)
        self.assertEqual(ov2["baseline"]["content_hash"], ov["baseline"]["content_hash"])
        self.assertEqual(ov2["project"]["id"], pid)


class TestDirtyAndWorktree(VP2Base):
    def test_dirty_preserved_byte_for_byte_and_worktree_created(self):
        repo = self._repo_under_allowlist(dirty=True)
        before_hash, before_map = _tree_hash(repo)
        ov = self.svc.connect_project("syn", "local_git", path=repo)
        self.assertEqual(ov["state"], "dirty")
        self.assertTrue(ov["baseline"]["dirty"])
        self.assertGreaterEqual(ov["baseline"]["untracked"], 1)
        pid = ov["project"]["id"]
        # создать безопасный worktree
        ov2 = self.svc.create_worktree(pid, "atlas/vp-2-demo")
        self.assertEqual(len(ov2["worktrees"]), 1)
        wt = ov2["worktrees"][0]
        self.assertTrue(wt["path"].startswith(self.svc.roots.worktrees_root))
        self.assertTrue(os.path.isdir(wt["path"]))
        # оригинал НЕ изменён байт-в-байт (dirty сохранён)
        after_hash, after_map = _tree_hash(repo)
        self.assertEqual(before_hash, after_hash, "dirty-состояние изменилось!")
        self.assertEqual(before_map, after_map)
        # git по-прежнему видит dirty (не было reset/clean)
        st = _git(repo, "status", "--porcelain").stdout
        self.assertIn("NEW_UNTRACKED.txt", st)

    def test_invalid_branch_rejected(self):
        repo = self._repo_under_allowlist()
        ov = self.svc.connect_project("syn", "local_git", path=repo)
        pid = ov["project"]["id"]
        from atlas_core.worktrees import WorktreeError
        for bad in ("main", "feature/x", "atlas/vp2-x", "atlas/vp-2-Bad_Slug"):
            with self.assertRaises(WorktreeError):
                self.svc.create_worktree(pid, bad)

    def test_no_overwrite_existing_worktree(self):
        repo = self._repo_under_allowlist()
        ov = self.svc.connect_project("syn", "local_git", path=repo)
        pid = ov["project"]["id"]
        self.svc.create_worktree(pid, "atlas/vp-2-demo")
        # повторное создание той же ветки → отказ (после release нет второго writer,
        # но каталог/ветка существуют)
        with self.assertRaises(Exception):
            self.svc.create_worktree(pid, "atlas/vp-2-demo")


class TestLeases(VP2Base):
    def test_second_writer_denied(self):
        repo = self._repo_under_allowlist()
        ov = self.svc.connect_project("syn", "local_git", path=repo)
        pid = ov["project"]["id"]
        ov2 = self.svc.create_worktree(pid, "atlas/vp-2-demo")
        wt_path = ov2["worktrees"][0]["path"]
        # аренда активна после создания
        self.assertTrue(ov2["lease"]["active"])
        from atlas_core.errors import AtlasError, ErrorCode
        with self.assertRaises(AtlasError) as cm:
            self.svc.try_acquire_writer(pid, wt_path, holder="builder2")
        self.assertEqual(cm.exception.classified.code, ErrorCode.WORKTREE_CONFLICT)

    def test_reconcile_requires_process_and_git_checks(self):
        from atlas_core.wsleases import WorktreeLeaseService
        svc = WorktreeLeaseService(self.settings.db_path, ttl_s=0.01, stale_grace_s=0.0)
        try:
            svc.acquire(project_id="p1", worktree="/wt/x", holder="b1")
            import time
            time.sleep(0.05)  # аренда просрочена + heartbeat stale
            # процесс жив → reconcile отклонён
            self.assertFalse(svc.reconcile(worktree="/wt/x",
                             process_alive=lambda _l: True, git_clean=lambda _l: True))
            # git грязный → reconcile отклонён
            self.assertFalse(svc.reconcile(worktree="/wt/x",
                             process_alive=lambda _l: False, git_clean=lambda _l: False))
            # процесс мёртв и git чист → освобождение
            self.assertTrue(svc.reconcile(worktree="/wt/x",
                            process_alive=lambda _l: False, git_clean=lambda _l: True))
            self.assertEqual(svc.active_count("/wt/x"), 0)
        finally:
            svc.close()


class TestSourcesAndDisconnect(VP2Base):
    def test_github_sanitize_and_credentials_rejected(self):
        from atlas_core.workspace import WorkspaceError, parse_github
        url, ref = parse_github("https://github.com/CodeVinci8/codevinci-atlas.git")
        self.assertEqual(url, "https://github.com/CodeVinci8/codevinci-atlas")
        self.assertEqual(ref, "CodeVinci8/codevinci-atlas")
        self.assertEqual(parse_github("owner/repo")[1], "owner/repo")
        with self.assertRaises(WorkspaceError):
            parse_github("https://user:ghp_secrettoken0000@github.com/o/r.git")
        # github source persists sanitized metadata, no credentials
        ov = self.svc.connect_project("gh", "github",
                                      github_ref="https://github.com/o/r.git")
        self.assertEqual(ov["project"]["source_kind"], "github")
        self.assertEqual(ov["project"]["source_location"], "https://github.com/o/r")
        self.assertEqual(ov["state"], "pending")

    def test_empty_project(self):
        ov = self.svc.connect_project("blank", "empty")
        self.assertEqual(ov["state"], "empty")
        self.assertIsNone(ov["baseline"])

    def test_disconnect_no_delete(self):
        repo = self._repo_under_allowlist(dirty=True)
        ov = self.svc.connect_project("syn", "local_git", path=repo)
        pid = ov["project"]["id"]
        ov2 = self.svc.create_worktree(pid, "atlas/vp-2-demo")
        wt_path = ov2["worktrees"][0]["path"]
        before_hash, _ = _tree_hash(repo)
        ovd = self.svc.disconnect_project(pid)
        self.assertEqual(ovd["state"], "disconnected")
        # источник, dirty-работа и worktree на диске сохранены
        self.assertTrue(os.path.isdir(repo))
        self.assertTrue(os.path.exists(os.path.join(repo, "NEW_UNTRACKED.txt")))
        self.assertTrue(os.path.isdir(wt_path))
        after_hash, _ = _tree_hash(repo)
        self.assertEqual(before_hash, after_hash)


class TestProjectsAPI(VP2Base):
    def setUp(self):
        super().setUp()
        from atlas_core.app import create_app
        from fastapi.testclient import TestClient
        self.client = TestClient(create_app(self.settings))

    def test_full_api_flow(self):
        repo = self._repo_under_allowlist(dirty=True)
        # список пуст
        self.assertEqual(self.client.get("/api/v1/projects").json()["projects"], [])
        # подключить local_git
        r = self.client.post("/api/v1/projects",
                             json={"name": "syn", "source_kind": "local_git", "path": repo})
        self.assertEqual(r.status_code, 201, r.text)
        ov = r.json()
        pid = ov["project"]["id"]
        self.assertEqual(ov["state"], "dirty")
        # overview
        self.assertEqual(self.client.get(f"/api/v1/projects/{pid}").json()["state"], "dirty")
        # worktree
        r2 = self.client.post(f"/api/v1/projects/{pid}/worktrees",
                              json={"branch": "atlas/vp-2-demo"})
        self.assertEqual(r2.status_code, 201, r2.text)
        wt_path = r2.json()["worktrees"][0]["path"]
        # второй writer → 409
        r3 = self.client.post(f"/api/v1/projects/{pid}/worktrees/acquire",
                              json={"worktree_path": wt_path})
        self.assertEqual(r3.status_code, 409, r3.text)
        self.assertEqual(r3.json()["error"]["code"], "WORKTREE_CONFLICT")
        # плохая ветка → 400
        r4 = self.client.post(f"/api/v1/projects/{pid}/worktrees", json={"branch": "nope"})
        self.assertEqual(r4.status_code, 400, r4.text)
        # disconnect → без удаления
        r5 = self.client.delete(f"/api/v1/projects/{pid}")
        self.assertEqual(r5.json()["state"], "disconnected")
        self.assertTrue(os.path.exists(os.path.join(repo, "NEW_UNTRACKED.txt")))

    def test_traversal_source_rejected_via_api(self):
        r = self.client.post("/api/v1/projects", json={
            "name": "evil", "source_kind": "local_git",
            "path": os.path.join(self.svc.roots.projects_root, "..", "..", "etc")})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "PATH_TRAVERSAL")


if __name__ == "__main__":
    unittest.main()
