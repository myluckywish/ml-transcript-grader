"use client";

import React, { useState } from "react";
import styles from "./page.module.css";

const API_BASE = process.env.NEXT_PUBLIC_PARSER_API_BASE ?? "http://127.0.0.1:8000";
const BATCH_SUBMIT_URL = `${API_BASE}/transcript/batches/submit`;
const ANALYZE_POLL_INTERVAL_MS = Number(process.env.NEXT_PUBLIC_ANALYZE_POLL_INTERVAL_MS ?? "1500");
const ANALYZE_MAX_WAIT_MS = Number(process.env.NEXT_PUBLIC_ANALYZE_MAX_WAIT_MS ?? "600000");
const MAX_BATCH_FILES = Math.max(1, Number(process.env.NEXT_PUBLIC_MAX_BATCH_FILES ?? "30"));

type CourseResult = {
  course_title?: string;
  subject?: string;
  units?: number | null;
  credit?: number | null;
  grade?: string | null;
  grade_points?: number | null;
};

type AnalyzeResponse = {
  filename?: string;
  mime_type?: string;
  characters?: number;
  courses?: CourseResult[];
  totals_by_category?: Record<string, number | null>;
  unweighted_gpa?: number | null;
  current_school_grade?: string | null;
  warnings?: string[];
  classification_provider?: {
    error?: string | null;
  };
};

type ApiErrorBody = {
  detail?: unknown;
};

type BatchSubmitResponse = {
  batch_id?: string;
  status?: string;
};

type BatchJob = {
  job_id?: string;
  status?: string;
  filename?: string;
  error?: string | null;
  result?: AnalyzeResponse | null;
};

type BatchStatusResponse = {
  batch_id?: string;
  status?: string;
  progress?: {
    completed?: number;
    total?: number;
    percent?: number;
    queued?: number;
    running?: number;
    succeeded?: number;
    failed?: number;
    skipped?: number;
  };
  jobs?: BatchJob[];
};

function formatApiError(detail: unknown, fallback: string): string {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      return fallback;
    }
  }
  return String(detail);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function isNonCountedGrade(grade?: string | null): boolean {
  const normalized = String(grade ?? "").trim().toUpperCase();
  return normalized === "F" || normalized === "U" || normalized === "E";
}

function toNumber(value?: number | string | null): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value.trim());
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function getCourseUnits(course: CourseResult): number {
  const units = toNumber(course.units);
  if (units !== null && units > 0) return units;
  const credit = toNumber(course.credit);
  if (credit !== null && credit > 0) return credit / 0.5;
  return 0;
}

function normalizeCourseTitle(value?: string | null): string {
  if (!value) return "";
  return value
    .toUpperCase()
    .trim()
    .replace(/\b(SEMESTER|SEM|S)[\s\-_:]*(1|2)\b/g, " ")
    .replace(/\b(FALL|SPRING|WINTER|SUMMER)\b/g, " ")
    .replace(/\b(Q1|Q2|Q3|Q4|TRI1|TRI2|TRI3)\b/g, " ")
    .replace(/\b(QUARTER|QTR|TRIMESTER|TERM)[\s\-_:]*(1|2|3|4)\b/g, " ")
    .replace(/\b(PERIOD|PD)\s*\d+\b/g, " ")
    .replace(/\b\d+(\.\d+)?\s*(CR|CREDIT|CREDITS)\b/g, " ")
    .replace(/\b(A|B)\b$/g, " ")
    .replace(/[^A-Z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function buildCourseDedupeKey(course: CourseResult): string {
  const normalizedTitle = normalizeCourseTitle(course.course_title);
  if (normalizedTitle) return normalizedTitle;

  return `MISSING|${String(course.subject ?? "other").trim().toLowerCase()}|${getCourseUnits(course)}|${String(course.grade ?? "").trim().toUpperCase()}`;
}

function dedupeCourses(courses: CourseResult[] | undefined): CourseResult[] {
  const deduped = new Map<string, CourseResult>();

  for (const course of courses ?? []) {
    const key = buildCourseDedupeKey(course);
    const existing = deduped.get(key);
    if (!existing || getCourseUnits(course) > getCourseUnits(existing)) {
      deduped.set(key, course);
    }
  }

  return Array.from(deduped.values());
}

const CATEGORY_OPTIONS = [
  "english",
  "mathematics",
  "natural_sciences",
  "social_sciences",
  "foreign_language",
  "other_units",
  "other",
] as const;

function getCountedCoursesForCategory(courses: CourseResult[] | undefined, category: string): CourseResult[] {
  return dedupeCourses(courses)
    .filter((course) => (course.subject ?? "").toLowerCase() === category)
    .filter((course) => !isNonCountedGrade(course.grade));
}

export default function App() {
  const [selectedNames, setSelectedNames] = useState<string[]>([]);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [batchStatus, setBatchStatus] = useState<BatchStatusResponse | null>(null);
  const [selectedCategory, setSelectedCategory] = useState("mathematics");

  async function analyzeBatch(fileList?: FileList | null) {
    const files = Array.from(fileList ?? []);
    if (files.length === 0) return;
    if (files.length > MAX_BATCH_FILES) {
      setError(`You can upload up to ${MAX_BATCH_FILES} files per batch.`);
      return;
    }

    setSelectedNames(files.map((file) => file.name));
    setError(null);
    setBatchStatus(null);

    const payload = new FormData();
    files.forEach((file) => payload.append("files", file));

    setIsAnalyzing(true);
    try {
      const submitRes = await fetch(BATCH_SUBMIT_URL, { method: "POST", body: payload });
      const submitData: BatchSubmitResponse | ApiErrorBody = await submitRes.json();
      if (!submitRes.ok) {
        setError(formatApiError((submitData as ApiErrorBody).detail, "Could not queue transcript batch."));
        return;
      }
      const batchId = (submitData as BatchSubmitResponse).batch_id;
      if (!batchId) {
        setError("Transcript batch did not return a batch id.");
        return;
      }

      const startedAt = Date.now();
      while (Date.now() - startedAt < ANALYZE_MAX_WAIT_MS) {
        const statusRes = await fetch(`${API_BASE}/transcript/batches/${batchId}`);
        const statusData: BatchStatusResponse | ApiErrorBody = await statusRes.json();
        if (!statusRes.ok) {
          setError(formatApiError((statusData as ApiErrorBody).detail, "Failed to read transcript batch status."));
          return;
        }
        const batch = statusData as BatchStatusResponse;
        setBatchStatus(batch);

        if (batch.status === "succeeded" || batch.status === "completed_with_errors") {
          return;
        }
        await sleep(ANALYZE_POLL_INTERVAL_MS);
      }

      setError(`Analysis exceeded ${Math.round(ANALYZE_MAX_WAIT_MS / 1000)} seconds.`);
    } catch (err: unknown) {
      console.error(err);
      setError("Could not reach backend. Start FastAPI on port 8000.");
    } finally {
      setIsAnalyzing(false);
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.headerCard}>
        <h1 className={styles.title}>Transcript Parser</h1>
        <p className={styles.subtitle}>Upload up to {MAX_BATCH_FILES} transcripts, then expand each one to review all counted attributes.</p>
      </section>

      <section className={styles.uploadCard}>
        <div
          className={styles.dropZone}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            void analyzeBatch(e.dataTransfer.files);
          }}
          onClick={() => document.getElementById("transcript-files")?.click()}
        >
          <input
            id="transcript-files"
            type="file"
            className={styles.hiddenInput}
            multiple
            onChange={(e) => void analyzeBatch(e.target.files)}
          />
          <strong>Drop transcripts or click to upload</strong>
          <span>Up to {MAX_BATCH_FILES} files per batch</span>
        </div>

        {selectedNames.length > 0 && (
          <p className={styles.meta}>Selected: {selectedNames.length} file(s)</p>
        )}

        {isAnalyzing && (
          <p className={styles.meta}>
            Batch status: {batchStatus?.status ?? "queued"} ({batchStatus?.progress?.percent ?? 0}%)
          </p>
        )}

        {batchStatus?.progress && (
          <p className={styles.meta}>
            Completed {batchStatus.progress.completed ?? 0}/{batchStatus.progress.total ?? 0} | queued {batchStatus.progress.queued ?? 0} |
            running {batchStatus.progress.running ?? 0} | succeeded {batchStatus.progress.succeeded ?? 0} | failed {batchStatus.progress.failed ?? 0}
          </p>
        )}

        {error && <p className={styles.error}>{error}</p>}
      </section>

      <section className={styles.card}>
        <h2>Batch Results</h2>
        {!batchStatus?.jobs?.length && <p className={styles.meta}>No results yet.</p>}
        <div className={styles.resultsStack}>
          {(batchStatus?.jobs ?? []).map((job, idx) => {
            const result = job.result ?? null;
            const categoryCounts = Object.fromEntries(
              CATEGORY_OPTIONS.map((category) => [category, getCountedCoursesForCategory(result?.courses, category).length]),
            );
            const countedCourses = getCountedCoursesForCategory(result?.courses, selectedCategory);

            return (
              <details key={`${job.job_id ?? "job"}-${idx}`} className={styles.transcriptItem}>
                <summary>
                  <span>{job.filename ?? result?.filename ?? `Transcript ${idx + 1}`}</span>
                  <strong>{job.status ?? "unknown"}</strong>
                </summary>

                {job.error && <p className={styles.error}>Error: {job.error}</p>}

                {result?.warnings?.map((warning, warningIdx) => (
                  <p key={`${warning}-${warningIdx}`} className={styles.meta}>{warning}</p>
                ))}

                {result?.classification_provider?.error && (
                  <p className={styles.error}>Classification issue: {result.classification_provider.error}</p>
                )}

                {result && (
                  <>
                    <div className={styles.statGrid}>
                      <div className={styles.statTile}>
                        <span>Unweighted GPA</span>
                        <strong>{result.unweighted_gpa ?? "N/A"}</strong>
                      </div>
                      <div className={styles.statTile}>
                        <span>School Grade</span>
                        <strong>{result.current_school_grade ?? "Unknown"}</strong>
                      </div>
                    </div>

                    <h3 className={styles.sectionTitle}>Course Counts by Category</h3>
                    <ul className={styles.unitList}>
                      {Object.entries(categoryCounts).map(([subject, value]) => (
                        <li key={subject}>
                          <span>{subject}</span>
                          <strong>{value ?? "N/A"}</strong>
                        </li>
                      ))}
                    </ul>

                    <h3 className={styles.sectionTitle}>Counted Courses</h3>
                    <label className={styles.control}>
                      Category
                      <select value={selectedCategory} onChange={(e) => setSelectedCategory(e.target.value)}>
                        {CATEGORY_OPTIONS.map((category) => (
                          <option key={category} value={category}>{category}</option>
                        ))}
                      </select>
                    </label>
                    <ul className={styles.unitList}>
                      {countedCourses.map((course, courseIdx) => (
                        <li key={`${course.course_title ?? "course"}-${courseIdx}`}>
                          <span>{course.course_title ?? "Unnamed course"}</span>
                          <strong>{selectedCategory}</strong>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </details>
            );
          })}
        </div>
      </section>
    </main>
  );
}
