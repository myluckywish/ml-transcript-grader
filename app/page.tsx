"use client";

import React, { useState } from "react";
import styles from "./page.module.css";

const API_BASE = process.env.NEXT_PUBLIC_PARSER_API_BASE ?? "http://127.0.0.1:8000";
const ANALYZE_URL = `${API_BASE}/transcript/analyze`;
const ANALYZE_TIMEOUT_MS = Number(process.env.NEXT_PUBLIC_ANALYZE_TIMEOUT_MS ?? "90000");

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
  warnings?: string[];
  classification_provider?: {
    error?: string | null;
  };
  detail?: string;
};

type ApiErrorBody = {
  detail?: unknown;
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

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export default function App() {
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analyzeData, setAnalyzeData] = useState<AnalyzeResponse | null>(null);
  const [selectedCategory, setSelectedCategory] = useState("mathematics");

  async function analyzeTranscript(file?: File | null) {
    if (!file) return;
    setSelectedName(file.name);
    setError(null);
    setAnalyzeData(null);

    const payload = new FormData();
    payload.append("file", file);

    setIsAnalyzing(true);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), ANALYZE_TIMEOUT_MS);
    try {
      const res = await fetch(ANALYZE_URL, { method: "POST", body: payload, signal: controller.signal });
      const data: AnalyzeResponse | ApiErrorBody = await res.json();
      if (!res.ok) {
        setError(formatApiError(data.detail, "Transcript analysis failed."));
        return;
      }
      setAnalyzeData(data as AnalyzeResponse);
    } catch (err: unknown) {
      if (isAbortError(err)) {
        setError(`Analysis timed out after ${Math.round(ANALYZE_TIMEOUT_MS / 1000)} seconds.`);
        return;
      }
      setError("Could not reach backend. Start FastAPI on port 8000.");
    } finally {
      clearTimeout(timeoutId);
      setIsAnalyzing(false);
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.headerCard}>
        <h1 className={styles.title}>Transcript Parser</h1>
        <p className={styles.subtitle}>Upload transcript, then view category totals and unweighted GPA.</p>
      </section>

      <section className={styles.uploadCard}>
        <div
          className={styles.dropZone}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            void analyzeTranscript(e.dataTransfer.files?.[0]);
          }}
          onClick={() => document.getElementById("transcript-file")?.click()}
        >
          <input
            id="transcript-file"
            type="file"
            className={styles.hiddenInput}
            onChange={(e) => void analyzeTranscript(e.target.files?.[0])}
          />
          <strong>Drop transcript or click to upload</strong>
          <span>PDF, DOCX, TXT and other supported files</span>
        </div>

        {selectedName && <p className={styles.meta}>Selected: {selectedName}</p>}
        {isAnalyzing && <p className={styles.meta}>Analyzing transcript...</p>}
        {error && <p className={styles.error}>{error}</p>}
        {analyzeData?.warnings?.map((warning, idx) => (
          <p key={`${warning}-${idx}`} className={styles.meta}>{warning}</p>
        ))}
        {analyzeData?.classification_provider?.error && (
          <p className={styles.error}>Classification issue: {analyzeData.classification_provider.error}</p>
        )}
      </section>

      <section className={styles.gridTwo}>
        <article className={styles.card}>
          <h2>Unweighted GPA</h2>
          <div className={styles.statGrid}>
            <div className={styles.statTile}>
              <span>GPA (4.0)</span>
              <strong>{analyzeData?.unweighted_gpa ?? "N/A"}</strong>
            </div>
          </div>
        </article>

        <article className={styles.card}>
          <h2>Totals by Category</h2>
          <ul className={styles.unitList}>
            {Object.entries(analyzeData?.totals_by_category ?? {}).map(([subject, value]) => (
              <li key={subject}>
                <span>{subject}</span>
                <strong>{value ?? "N/A"}</strong>
              </li>
            ))}
          </ul>
        </article>
      </section>

      <section className={styles.card}>
        <h2>Counted Courses by Category</h2>
        <label className={styles.control}>
          Category
          <select value={selectedCategory} onChange={(e) => setSelectedCategory(e.target.value)}>
            <option value="english">english</option>
            <option value="mathematics">mathematics</option>
            <option value="natural_sciences">natural_sciences</option>
            <option value="social_sciences">social_sciences</option>
            <option value="foreign_language">foreign_language</option>
            <option value="other_units">other_units</option>
            <option value="other">other</option>
          </select>
        </label>
        <ul className={styles.unitList}>
          {(analyzeData?.courses ?? [])
            .filter((course) => (course.subject ?? "").toLowerCase() === selectedCategory)
            .map((course, index) => (
              <li key={`${course.course_title ?? "course"}-${index}`}>
                <span>{course.course_title ?? "Unnamed course"}</span>
                <strong>{selectedCategory}</strong>
              </li>
            ))}
        </ul>
      </section>
    </main>
  );
}
