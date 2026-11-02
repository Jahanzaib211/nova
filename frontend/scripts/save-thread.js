import { config } from "dotenv";
import fs from "fs";
import path from "path";

export async function main() {
  const url = new URL(process.argv[2]);
  const threadId = url.pathname.split("/").pop();
  const host = url.host;
  const apiURL = new URL(
    `/api/langgraph/threads/${threadId}/history`,
    `${url.protocol}//${host}`,
  );
  const response = await fetch(apiURL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      limit: 10,
    }),
  });

  const data = (await response.json())[0];
  if (!data) {
    console.error("No data found");
    return;
  }

  const title = data.values.title;

  const rootPath = path.resolve(process.cwd(), "public/threads", threadId);
  if (fs.existsSync(rootPath)) {
    fs.rmSync(rootPath, { recursive: true });
  }
  fs.mkdirSync(rootPath, { recursive: true });
  // CodeQL[js/http-to-file-access]: intentional — saving thread data to local filesystem
  fs.writeFileSync(
    path.resolve(rootPath, "thread.json"),
    JSON.stringify(data, null, 2),
  );
  // Data dir is `.nova`; fall back to the legacy `.deer-flow` if only it exists.
  const novaHome = path.resolve(process.cwd(), "../backend/.nova");
  const legacyHome = path.resolve(process.cwd(), "../backend/.deer-flow");
  const dataHome =
    !fs.existsSync(novaHome) && fs.existsSync(legacyHome)
      ? legacyHome
      : novaHome;
  const backendRootPath = path.resolve(dataHome, "threads", threadId);
  copyFolder("user-data/outputs", rootPath, backendRootPath);
  copyFolder("user-data/uploads", rootPath, backendRootPath);
  console.info(`Saved thread "${title}" to ${rootPath}`);
}

function copyFolder(relPath, rootPath, backendRootPath) {
  const outputsPath = path.resolve(backendRootPath, relPath);
  if (fs.existsSync(outputsPath)) {
    fs.cpSync(outputsPath, path.resolve(rootPath, relPath), {
      recursive: true,
    });
  }
}

config();
main();
