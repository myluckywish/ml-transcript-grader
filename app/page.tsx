"use client";

import React, { useState } from "react";
import styles from "./page.module.css";

const API_BASE = process.env.NEXT_PUBLIC_PARSER_API_BASE ?? "http://127.0.0.1:8000";
const BATCH_SUBMIT_URL = `${API_BASE}/transcript/batches/submit`;
const ANALYZE_POLL_INTERVAL_MS = Number(process.env.NEXT_PUBLIC_ANALYZE_POLL_INTERVAL_MS ?? "1500");
const ANALYZE_MAX_WAIT_MS = Number(process.env.NEXT_PUBLIC_ANALYZE_MAX_WAIT_MS ?? "600000");
const MAX_BATCH_FILES = Math.max(1, Number(process.env.NEXT_PUBLIC_MAX_BATCH_FILES ?? "30"));
const RIGOR_TOKEN_MAP: Record<string, string> = {
  H: "HONORS",
  HONORS: "HONORS",
  AP: "AP",
  IB: "IB",
  ADV: "ADVANCED",
  ADVANCED: "ADVANCED",
  PREAP: "PREAP",
  "PRE-AP": "PREAP",
};
const TITLE_ABBREVIATIONS: Record<string, string> = {
  ENG: "ENGLISH",
  ALG: "ALGEBRA",
  BIO: "BIOLOGY",
  CHEM: "CHEMISTRY",
  PHYS: "PHYSICS",
  GEO: "GEOMETRY",
  HIST: "HISTORY",
  GOV: "GOVERNMENT",
};
const ROMAN_NUMERAL_MAP: Record<string, string> = {
  I: "1",
  II: "2",
  III: "3",
  IV: "4",
  V: "5",
  VI: "6",
  VII: "7",
  VIII: "8",
  IX: "9",
  X: "10",
};
const COURSE_CODE_RE = /^[A-Z]{1,4}\d{2,4}[A-Z]?$/;
const FUZZY_DEDUPE_THRESHOLD = 0.85;

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
  credits_by_category?: Record<string, number | null>;
  unweighted_gpa?: number | null;
  current_school_grade?: string | null;
  warnings?: string[];
  classification_provider?: { error?: string | null };
};

type ApiErrorBody = { detail?: unknown };

type BatchSubmitResponse = { batch_id?: string; status?: string };

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
    try { return JSON.stringify(detail); } catch { return fallback; }
  }
  return String(detail);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

function isNonCountedGrade(grade?: string | null): boolean {
  const n = String(grade ?? "").trim().toUpperCase();
  return n === "F" || n === "U" || n === "E";
}

function toNumber(value?: number | string | null): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const p = Number(value.trim());
    return Number.isFinite(p) ? p : null;
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

function getCourseCreditValue(course: CourseResult): number {
  const credit = toNumber(course.credit);
  if (credit !== null && credit > 0) return credit;
  const units = toNumber(course.units);
  if (units !== null && units > 0) return units * 0.5;
  return 0;
}

function formatCredits(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function normalizeCourseTitle(value?: string | null): string {
  if (!value) return "";
  return value
    .toUpperCase()
    .trim()
    .replace(/\b(I|II|III|IV|V|VI|VII|VIII|IX|X)\b/g, (match) => ROMAN_NUMERAL_MAP[match] ?? match)
    .replace(/\b(SEMESTER|SEM|S)[\s\-_:]*(1|2)\b/g, " ")
    .replace(/\b(FALL|SPRING|WINTER|SUMMER)\b/g, " ")
    .replace(/\b(Q1|Q2|Q3|Q4|TRI1|TRI2|TRI3)\b/g, " ")
    .replace(/\b(QUARTER|QTR|TRIMESTER|TERM)[\s\-_:]*(1|2|3|4)\b/g, " ")
    .replace(/\b(PERIOD|PD)\s*\d+\b/g, " ")
    .replace(/\b\d+(\.\d+)?\s*(CR|CREDIT|CREDITS)\b/g, " ")
    .replace(/\b(A|B)\b$/g, " ")
    .replace(/(\d)\s*[AB]\b/g, "$1")
    .replace(/[^A-Z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function canonicalizeIdentityTokens(normalizedTitle: string): [string, string, string] {
  if (!normalizedTitle) return ["", "", ""];

  const rigorTokens: string[] = [];
  const baseTokens: string[] = [];
  const levelTokens: string[] = [];

  for (const token of normalizedTitle.split(" ")) {
    const mappedRigor = RIGOR_TOKEN_MAP[token];
    if (mappedRigor) {
      rigorTokens.push(mappedRigor);
      continue;
    }

    const expanded = TITLE_ABBREVIATIONS[token] ?? token;
    if (COURSE_CODE_RE.test(expanded)) {
      continue;
    }
    if (/^\d+$/.test(expanded)) {
      levelTokens.push(expanded);
      continue;
    }
    baseTokens.push(expanded);
  }

  const rigorKey = Array.from(new Set(rigorTokens)).sort().join("|");
  const baseKey = baseTokens.join(" ");
  const levelKey = levelTokens.join(" ");
  return [baseKey, levelKey, rigorKey];
}

function courseIdentity(course: CourseResult): [string, string, string] {
  return canonicalizeIdentityTokens(normalizeCourseTitle(course.course_title));
}

function unitsAreCompatible(left: CourseResult, right: CourseResult): boolean {
  const leftUnits = getCourseUnits(left);
  const rightUnits = getCourseUnits(right);
  if (!leftUnits || !rightUnits) return true;
  return Math.abs(leftUnits - rightUnits) <= 0.51 || Math.max(leftUnits, rightUnits) >= 1.0;
}

function similarity(a: string, b: string): number {
  if (a === b) return 1;
  if (!a.length || !b.length) return 0;

  const rows = a.length + 1;
  const cols = b.length + 1;
  const dp: number[][] = Array.from({ length: rows }, (_, i) =>
    Array.from({ length: cols }, (_, j) => (i === 0 ? j : j === 0 ? i : 0))
  );

  for (let i = 1; i < rows; i += 1) {
    for (let j = 1; j < cols; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      dp[i][j] = Math.min(
        dp[i - 1][j] + 1,
        dp[i][j - 1] + 1,
        dp[i - 1][j - 1] + cost
      );
    }
  }

  const distance = dp[a.length][b.length];
  return 1 - distance / Math.max(a.length, b.length);
}

function shouldFuzzyDedupe(course: CourseResult, candidate: CourseResult): boolean {
  const [baseKey, levelKey, rigorKey] = courseIdentity(course);
  const [candidateBase, candidateLevel, candidateRigor] = courseIdentity(candidate);
  if (!baseKey || !candidateBase) return false;
  if (levelKey !== candidateLevel || rigorKey !== candidateRigor) return false;
  if ((course.subject ?? "").toLowerCase() !== (candidate.subject ?? "").toLowerCase()) return false;
  if (!unitsAreCompatible(course, candidate)) return false;
  return similarity(baseKey, candidateBase) >= FUZZY_DEDUPE_THRESHOLD;
}

function buildCourseDedupeKey(course: CourseResult): string {
  const t = normalizeCourseTitle(course.course_title);
  if (t) {
    const [baseKey, levelKey, rigorKey] = canonicalizeIdentityTokens(t);
    return `${baseKey}||${levelKey}||${rigorKey}`;
  }
  return `MISSING|${String(course.subject ?? "other").trim().toLowerCase()}|${getCourseUnits(course)}|${String(course.grade ?? "").trim().toUpperCase()}`;
}

function dedupeCourses(courses: CourseResult[] | undefined): CourseResult[] {
  const deduped = new Map<string, CourseResult>();
  for (const course of courses ?? []) {
    let key = buildCourseDedupeKey(course);
    let existing = deduped.get(key);

    if (!existing) {
      for (const [candidateKey, candidate] of deduped.entries()) {
        if (shouldFuzzyDedupe(course, candidate)) {
          key = candidateKey;
          existing = candidate;
          break;
        }
      }
    }

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

const CATEGORY_LABELS: Record<string, string> = {
  english: "English",
  mathematics: "Math",
  natural_sciences: "Science",
  social_sciences: "Social Studies",
  foreign_language: "Language",
  other_units: "Other Units",
  other: "Other",
};

function getCountedCoursesForCategory(courses: CourseResult[] | undefined, category: string): CourseResult[] {
  return dedupeCourses(courses)
    .filter((c) => (c.subject ?? "").toLowerCase() === category)
    .filter((c) => !isNonCountedGrade(c.grade));
}

function getCountedCreditsForCategory(courses: CourseResult[] | undefined, category: string): number {
  return getCountedCoursesForCategory(courses, category)
    .reduce((sum, course) => sum + getCourseCreditValue(course), 0);
}

function getAllCoursesForCategory(courses: CourseResult[] | undefined, category: string): CourseResult[] {
  return (courses ?? [])
    .filter((c) => (c.subject ?? "").toLowerCase() === category)
    .filter((c) => !isNonCountedGrade(c.grade));
}

function TranscriptCard({ job, idx }: { job: BatchJob; idx: number }) {
  const [open, setOpen] = useState(false);
  const [activeCategory, setActiveCategory] = useState<string>(CATEGORY_OPTIONS[0]);

  const result = job.result ?? null;
  const categoryCounts = Object.fromEntries(
    CATEGORY_OPTIONS.map((cat) => [cat, getCountedCoursesForCategory(result?.courses, cat).length])
  );
  const categoryCredits = Object.fromEntries(
    CATEGORY_OPTIONS.map((cat) => [
      cat,
      toNumber(result?.credits_by_category?.[cat]) ?? getCountedCreditsForCategory(result?.courses, cat),
    ])
  );
  const gpa = result?.unweighted_gpa;
  const gpaClass =
    gpa == null ? "" : gpa >= 3.5 ? styles.gpaHigh : gpa >= 2.5 ? styles.gpaMid : styles.gpaLow;
  const badgeClass =
    job.status === "succeeded" ? styles.badgeOk :
    job.status === "failed" ? styles.badgeFail :
    styles.badgePending;
  const visibleCourses = getAllCoursesForCategory(result?.courses, activeCategory);

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <span className={styles.filename}>{job.filename ?? `Transcript ${idx + 1}`}</span>
        <span className={`${styles.badge} ${badgeClass}`}>{job.status ?? "pending"}</span>
      </div>

      {job.error && <p className={styles.inlineError}>{job.error}</p>}

      {result && (
        <>
          <div className={styles.heroRow}>
            <div className={styles.heroStat}>
              <span className={styles.heroLabel}>Unweighted GPA</span>
              <span className={`${styles.heroValue} ${gpaClass}`}>
                {gpa != null ? gpa.toFixed(2) : "N/A"}
              </span>
            </div>
            <div className={styles.divider} />
            <div className={styles.heroStat}>
              <span className={styles.heroLabel}>Grade Level</span>
              <span className={styles.heroValue}>{result.current_school_grade ?? "Unknown"}</span>
            </div>
          </div>

          <div className={styles.categoryRow}>
            {CATEGORY_OPTIONS.map((cat) => (
              <div key={cat} className={styles.catChip}>
                <span className={styles.catLabel}>{CATEGORY_LABELS[cat]}</span>
                <span className={styles.catCount}>{formatCredits(categoryCredits[cat])}</span>
              </div>
            ))}
          </div>

          <button className={styles.detailsToggle} onClick={() => setOpen((v) => !v)}>
            <span className={`${styles.chevron} ${open ? styles.chevronOpen : ""}`}>›</span>
            {open ? "Hide courses" : "Show courses"}
          </button>

          {open && (
            <div className={styles.detailsPanel}>
              {result.warnings?.map((w, i) => (
                <p key={i} className={styles.warnMsg}>{w}</p>
              ))}
              {result.classification_provider?.error && (
                <p className={styles.inlineError}>Classification: {result.classification_provider.error}</p>
              )}
              <div className={styles.tabs}>
                {CATEGORY_OPTIONS.map((cat) => (
                  <button
                    key={cat}
                    className={`${styles.tab} ${activeCategory === cat ? styles.tabActive : ""}`}
                    onClick={() => setActiveCategory(cat)}
                  >
                    {CATEGORY_LABELS[cat]}
                    <span className={styles.tabCount}>{categoryCounts[cat]}</span>
                  </button>
                ))}
              </div>
              <ul className={styles.courseList}>
                {visibleCourses.length === 0 ? (
                  <li className={styles.emptyMsg}>No courses in this category</li>
                ) : (
                  visibleCourses.map((course, i) => (
                    <li key={i} className={styles.courseItem}>
                      <span>{course.course_title ?? "Unnamed"}</span>
                      {course.grade && <span className={styles.courseGrade}>{course.grade}</span>}
                    </li>
                  ))
                )}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function App() {
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [batchStatus, setBatchStatus] = useState<BatchStatusResponse | null>(null);

  async function analyzeBatch(fileList?: FileList | null) {
    const files = Array.from(fileList ?? []);
    if (files.length === 0) return;
    if (files.length > MAX_BATCH_FILES) {
      setError(`Maximum ${MAX_BATCH_FILES} files per batch.`);
      return;
    }

    setError(null);
    setBatchStatus(null);
    setIsAnalyzing(true);

    const payload = new FormData();
    files.forEach((file) => payload.append("files", file));

    try {
      const submitRes = await fetch(BATCH_SUBMIT_URL, { method: "POST", body: payload });
      const submitData: BatchSubmitResponse | ApiErrorBody = await submitRes.json();
      if (!submitRes.ok) {
        setError(formatApiError((submitData as ApiErrorBody).detail, "Could not queue batch."));
        return;
      }
      const batchId = (submitData as BatchSubmitResponse).batch_id;
      if (!batchId) { setError("No batch ID returned."); return; }

      const startedAt = Date.now();
      while (Date.now() - startedAt < ANALYZE_MAX_WAIT_MS) {
        const statusRes = await fetch(`${API_BASE}/transcript/batches/${batchId}`);
        const statusData: BatchStatusResponse | ApiErrorBody = await statusRes.json();
        if (!statusRes.ok) {
          setError(formatApiError((statusData as ApiErrorBody).detail, "Failed to read batch status."));
          return;
        }
        const batch = statusData as BatchStatusResponse;
        setBatchStatus(batch);
        if (batch.status === "succeeded" || batch.status === "completed_with_errors") return;
        await sleep(ANALYZE_POLL_INTERVAL_MS);
      }
      setError(`Analysis exceeded ${Math.round(ANALYZE_MAX_WAIT_MS / 1000)} seconds.`);
    } catch (err) {
      console.error(err);
      setError("Could not reach backend. Start FastAPI on port 8000.");
    } finally {
      setIsAnalyzing(false);
    }
  }

  const progress = batchStatus?.progress;

  return (
    <div className={styles.root}>
      <div className={styles.inner}>
        <header className={styles.header}>
          <p className={styles.appTitle}>Transcript Grader</p>
          <p className={styles.appSub}>Upload transcripts to extract GPA and course counts</p>
        </header>

        <div
          className={styles.dropZone}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); void analyzeBatch(e.dataTransfer.files); }}
          onClick={() => document.getElementById("transcript-files")?.click()}
        >
          <input
            id="transcript-files"
            type="file"
            className={styles.hiddenInput}
            multiple
            onChange={(e) => void analyzeBatch(e.target.files)}
          />
          <span className={styles.dropLabel}>
            {isAnalyzing ? "Analyzing..." : "Drop transcripts here or click to upload"}
          </span>
          <span className={styles.dropSub}>Up to {MAX_BATCH_FILES} PDFs per batch</span>
        </div>

        {isAnalyzing && progress && (
          <div className={styles.progressWrap}>
            <div className={styles.progressBar}>
              <div className={styles.progressFill} style={{ width: `${progress.percent ?? 0}%` }} />
            </div>
            <span className={styles.progressText}>
              {progress.completed ?? 0} / {progress.total ?? 0} processed
            </span>
          </div>
        )}

        {error && <p className={styles.globalError}>{error}</p>}

        {(batchStatus?.jobs?.length ?? 0) > 0 ? (
          <div className={styles.results}>
            {(batchStatus?.jobs ?? []).map((job, idx) => (
              <TranscriptCard key={job.job_id ?? idx} job={job} idx={idx} />
            ))}
          </div>
        ) : !isAnalyzing && (
          <p className={styles.emptyState}>No transcripts analyzed yet.</p>
        )}
      </div>
    </div>
  );
}
