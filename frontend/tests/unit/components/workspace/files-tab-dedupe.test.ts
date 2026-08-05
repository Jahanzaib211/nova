/**
 * Files tab: a file must appear exactly once.
 *
 * The panel renders two sections — "Outputs" (presented deliverables, from
 * `artifacts`) and the repository tree (everything on disk, from
 * `GET /api/sandbox/files`). The backend's file listing walks the workspace,
 * outputs AND uploads roots, so every presented deliverable was rendered in
 * both sections. Reported from live use as "files listed twice in the UI".
 */

import { describe, expect, test } from "vitest";

import { selectTreeFiles } from "@/components/workspace/agent-computer/files-tab";
import type { SandboxFile } from "@/core/sandbox/hooks";

function file(virtual_path: string): SandboxFile {
  const name = virtual_path.split("/").at(-1)!;
  return {
    path: `/host${virtual_path}`,
    virtual_path,
    name,
    size: 10,
    mtime: 1,
    modified: "12:00:00",
  };
}

const WORKSPACE = file("/mnt/user-data/workspace/src/index.ts");
const OUTPUT = file("/mnt/user-data/outputs/report.pdf");
const OUTPUT_2 = file("/mnt/user-data/outputs/chart.png");
const UPLOAD = file("/mnt/user-data/uploads/notes.txt");

describe("selectTreeFiles", () => {
  test("removes presented deliverables from the tree", () => {
    const out = selectTreeFiles(
      [WORKSPACE, OUTPUT, UPLOAD],
      ["/mnt/user-data/outputs/report.pdf"],
    );
    expect(out.map((f) => f.virtual_path)).toEqual([
      "/mnt/user-data/workspace/src/index.ts",
      "/mnt/user-data/uploads/notes.txt",
    ]);
  });

  test("a presented file is rendered in exactly one section", () => {
    const artifacts = ["/mnt/user-data/outputs/report.pdf"];
    const tree = selectTreeFiles([WORKSPACE, OUTPUT], artifacts);
    const rendered = [...tree.map((f) => f.virtual_path), ...artifacts];
    expect(new Set(rendered).size).toBe(rendered.length);
  });

  test("keeps outputs that were never presented", () => {
    // The agent wrote it to outputs/ but never called present_files — it still
    // belongs in the tree, or it would vanish from the UI entirely.
    const out = selectTreeFiles(
      [OUTPUT, OUTPUT_2],
      ["/mnt/user-data/outputs/report.pdf"],
    );
    expect(out.map((f) => f.virtual_path)).toEqual([
      "/mnt/user-data/outputs/chart.png",
    ]);
  });

  test("returns the input untouched when nothing is presented", () => {
    const files = [WORKSPACE, OUTPUT, UPLOAD];
    expect(selectTreeFiles(files, [])).toBe(files);
  });

  test("an artifact with no matching file on disk is harmless", () => {
    const out = selectTreeFiles([WORKSPACE], ["/mnt/user-data/outputs/gone.pdf"]);
    expect(out).toEqual([WORKSPACE]);
  });

  test("matching is exact — a same-named workspace file is not swallowed", () => {
    const sameName = file("/mnt/user-data/workspace/report.pdf");
    const out = selectTreeFiles(
      [sameName, OUTPUT],
      ["/mnt/user-data/outputs/report.pdf"],
    );
    expect(out.map((f) => f.virtual_path)).toEqual([
      "/mnt/user-data/workspace/report.pdf",
    ]);
  });
});
