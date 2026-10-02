// Загрузка файла частями: init → PUT частей (S3 multipart через API) → complete.

import { API_PREFIX, ApiError, api } from "@/lib/api";
import type { ProjectDocument } from "@/lib/types";

interface InitOut {
  upload_id: string;
  chunk_size: number;
  parts_total: number;
}

export interface UploadProgress {
  loaded: number;
  total: number;
}

const PARALLEL_PARTS = 3;

async function putPart(projectId: string, uploadId: string, partNo: number, blob: Blob, signal?: AbortSignal) {
  for (let attempt = 0; ; attempt++) {
    const resp = await fetch(`${API_PREFIX}/projects/${projectId}/uploads/${uploadId}/parts/${partNo}`, {
      method: "PUT",
      body: blob,
      credentials: "same-origin",
      signal,
    });
    if (resp.ok) return;
    // сетевые сбои и 5xx — до трёх повторов с паузой; 4xx — сразу ошибка
    if (resp.status < 500 || attempt >= 2) {
      const body = await resp.json().catch(() => null);
      throw new ApiError(resp.status, body?.detail ?? body);
    }
    await new Promise((r) => setTimeout(r, 1000 * 2 ** attempt));
  }
}

export async function uploadFile(
  projectId: string,
  file: File,
  relativePath: string,
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<ProjectDocument> {
  const init = await api.post<InitOut>(`/projects/${projectId}/uploads`, {
    filename: file.name,
    size: file.size,
    relative_path: relativePath,
  });
  let loaded = 0;
  onProgress({ loaded, total: file.size });
  const parts = Array.from({ length: init.parts_total }, (_, i) => i + 1);
  try {
    for (let i = 0; i < parts.length; i += PARALLEL_PARTS) {
      await Promise.all(
        parts.slice(i, i + PARALLEL_PARTS).map(async (n) => {
          const blob = file.slice((n - 1) * init.chunk_size, n * init.chunk_size);
          await putPart(projectId, init.upload_id, n, blob, signal);
          loaded += blob.size;
          onProgress({ loaded, total: file.size });
        }),
      );
    }
    return await api.post<ProjectDocument>(`/projects/${projectId}/uploads/${init.upload_id}/complete`);
  } catch (err) {
    api.delete(`/projects/${projectId}/uploads/${init.upload_id}`).catch(() => undefined);
    throw err;
  }
}

/** Путь файла внутри перетащенной/выбранной папки (без имени файла). */
export function relativeDir(file: File & { path?: string; webkitRelativePath?: string }): string {
  const full = (file.path || file.webkitRelativePath || "").replace(/^\.?\//, "");
  const parts = full.split("/").filter(Boolean);
  return parts.length > 1 ? parts.slice(0, -1).join("/") : "";
}
