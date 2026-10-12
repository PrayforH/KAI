export interface UploadLimits {
  max_file_bytes: number;
  max_archive_file_bytes?: number;
  archive_extensions?: string[];
  max_files: number;
  max_total_bytes: number;
}

export function isArchiveUpload(name: string, limits: UploadLimits): boolean {
  return limits.archive_extensions?.some(extension => name.toLowerCase().endsWith(extension)) ?? false;
}

export function inputFileSizeLimit(name: string, limits: UploadLimits): number {
  return isArchiveUpload(name, limits) ? limits.max_archive_file_bytes ?? limits.max_file_bytes : limits.max_file_bytes;
}

export function inputTotalSizeLimit(names: string[], limits: UploadLimits): number {
  return names.some(name => isArchiveUpload(name, limits))
    ? Math.max(limits.max_total_bytes, limits.max_archive_file_bytes ?? limits.max_file_bytes)
    : limits.max_total_bytes;
}
