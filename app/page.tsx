"use client";

import React, { useEffect, useMemo, useState } from "react";
import styles from "./page.module.css";

const API_BASE = process.env.NEXT_PUBLIC_PARSER_API_BASE ?? "http://127.0.0.1:8000";
const ANALYZE_URL = `${API_BASE}/transcript/analyze`;
const CLASSIFY_URL = `${API_BASE}/units/classify-titles`;

type TranscriptAnalysis = {
  required_units?: Record<string, number | null>;
  gpa?: {
    reported_weighted?: number | null;
    unweighted_4_scale?: number | null;
    method?: string;
    scale_detected?: string | null;
  };
  confidence?: number | null;
  notes?: string[];
};

type AnalyzeResponse = {
  filename?: string;
  extracted_text?: string;
  analysis?: TranscriptAnalysis | null;
  analysis_error?: string | null;
  extraction_provider?: {
    name?: string;
    azure_doc_intel?: {
      enabled?: boolean;
      configured?: boolean;
      missing_settings?: string[];
      model_id?: string;
      error?: string | null;
    };
  };
  ai_provider?: {
    enabled?: boolean;
    configured?: boolean;
    missing_settings?: string[];
  };
  debug?: BackendDebug;
  detail?: string;
};

type ClassifiedCourse = {
  raw_title: string;
  normalized_title: string;
  subject: string | null;
  method: "mapping_lookup" | "rules" | "unknown_queued" | "ai_probabilities";
  confidence: number;
  unknown_queue_id: number | null;
  subject_probabilities?: Record<string, number>;
};

type ClassifyResponse = {
  classified_courses: ClassifiedCourse[];
  unit_counts: Record<string, number>;
  unknown_count: number;
  ai_provider?: {
    enabled?: boolean;
    configured?: boolean;
    missing_settings?: string[];
    ai_error?: string | null;
  };
  debug?: BackendDebug;
  detail?: string;
};

type BackendDebugStep = {
  step: number;
  label: string;
  elapsed_ms: number;
  meta: Record<string, unknown>;
};

type BackendDebug = {
  request_id: string;
  flow: string;
  steps: BackendDebugStep[];
  total_elapsed_ms: number;
};

type ApiErrorBody = {
  detail?: unknown;
};

type DebugEvent = {
  time: string;
  source: "ui" | "api";
  step: string;
  detail: string;
};

const SUBJECTS = [
  "english",
  "mathematics",
  "natural_sciences",
  "social_sciences",
  "other_units",
];

type ReviewStatus = "approved" | "review" | "disproved";

function inferReviewStatus(method: ClassifiedCourse["method"]): ReviewStatus {
  if (method === "mapping_lookup") return "approved";
  if (method === "rules" || method === "ai_probabilities") return "review";
  return "disproved";
}

function detectCourseLines(text: string): string[] {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const ignored = [
    "GPA",
    "CUMULATIVE",
    "TRANSCRIPT",
    "STUDENT",
    "ADDRESS",
    "PHONE",
    "DATE OF BIRTH",
    "DOB",
    "CREDITS ATTEMPTED",
    "CREDITS EARNED",
    "SCHOOL",
    "SEMESTER GPA",
  ];

  return lines
    .filter((line) => line.length >= 4 && line.length <= 70)
    .filter((line) => /[A-Za-z]/.test(line))
    .filter((line) => !/^\d+(\.\d+)?$/.test(line))
    .filter((line) => !ignored.some((word) => line.toUpperCase().includes(word)))
    .slice(0, 80);
}

function formatApiError(detail: unknown, fallback: string): string {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) return String(item.msg);
        return "";
      })
      .filter(Boolean);
    return messages.join("; ") || fallback;
  }
  if (typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      return fallback;
    }
  }
  return String(detail);
}

export default function App() {
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [schoolId, setSchoolId] = useState("");
  const [courseTitleText, setCourseTitleText] = useState("");

  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isClassifying, setIsClassifying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [analyzeData, setAnalyzeData] = useState<AnalyzeResponse | null>(null);
  const [classifyData, setClassifyData] = useState<ClassifyResponse | null>(null);
  const [resolveSubjectById, setResolveSubjectById] = useState<Record<number, string>>({});
  const [debugEvents, setDebugEvents] = useState<DebugEvent[]>([]);

  const parsedCourseTitles = useMemo(
    () =>
      courseTitleText
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean),
    [courseTitleText],
  );

  const statusCounts = useMemo(() => {
    const counts: Record<ReviewStatus, number> = { approved: 0, review: 0, disproved: 0 };
    for (const course of classifyData?.classified_courses ?? []) {
      counts[inferReviewStatus(course.method)] += 1;
    }
    return counts;
  }, [classifyData]);

  useEffect(() => {
    return () => {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    };
  }, [pdfUrl]);

  function pushDebug(source: "ui" | "api", step: string, detail: string) {
    setDebugEvents((prev) => {
      const next = [
        ...prev,
        {
          time: new Date().toISOString(),
          source,
          step,
          detail,
        },
      ];
      return next.slice(-200);
    });
  }

  function pushBackendDebug(debug: BackendDebug | undefined, endpointLabel: string) {
    if (!debug) return;
    pushDebug(
      "api",
      `${endpointLabel}:request`,
      `request_id=${debug.request_id} total_elapsed_ms=${debug.total_elapsed_ms}`,
    );
    for (const step of debug.steps) {
      pushDebug(
        "api",
        `${endpointLabel}:${step.step}.${step.label}`,
        `elapsed_ms=${step.elapsed_ms} meta=${JSON.stringify(step.meta)}`,
      );
    }
  }

  async function analyzeTranscript(file?: File | null) {
    if (!file) return;
    setDebugEvents([]);
    setSelectedName(file.name);
    setError(null);
    setAnalyzeData(null);
    setClassifyData(null);

    if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    setPdfUrl(file.type === "application/pdf" ? URL.createObjectURL(file) : null);

    const payload = new FormData();
    payload.append("file", file);
    pushDebug("ui", "analyze:start", `filename=${file.name} type=${file.type || "unknown"}`);

    setIsAnalyzing(true);
    try {
      const res = await fetch(`${ANALYZE_URL}?debug=true`, { method: "POST", body: payload });
      const data: AnalyzeResponse | ApiErrorBody = await res.json();
      if (!res.ok) {
        const message = formatApiError(data.detail, "Transcript analysis failed.");
        pushDebug("ui", "analyze:error", message);
        setError(message);
        return;
      }
      const successData = data as AnalyzeResponse;
      setAnalyzeData(successData);
      pushBackendDebug(successData.debug, "transcript_analyze");
      const candidates = detectCourseLines(successData.extracted_text ?? "");
      pushDebug("ui", "courses:detected", `candidate_count=${candidates.length}`);
      setCourseTitleText(candidates.join("\n"));
      if (candidates.length > 0) {
        await classifyTitles(candidates, schoolId);
      }
    } catch (requestError) {
      console.error("Analyze request failed", requestError);
      pushDebug("ui", "analyze:error", "Could not reach backend.");
      setError("Could not reach backend. Start FastAPI on port 8000.");
    } finally {
      setIsAnalyzing(false);
      pushDebug("ui", "analyze:done", "analyze request completed");
    }
  }

  async function classifyTitles(titles = parsedCourseTitles, chosenSchoolId = schoolId) {
    setIsClassifying(true);
    pushDebug("ui", "classify:start", `title_count=${titles.length} school_id=${chosenSchoolId || "global"}`);
    try {
      const res = await fetch(`${CLASSIFY_URL}?debug=true`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ titles, school_id: chosenSchoolId }),
      });
      const data: ClassifyResponse | ApiErrorBody = await res.json();
      if (!res.ok) {
        const message = formatApiError(data.detail, "Classification failed.");
        pushDebug("ui", "classify:error", message);
        setError(message);
        return;
      }
      const successData = data as ClassifyResponse;
      setClassifyData(successData);
      pushBackendDebug(successData.debug, "units_classify_titles");
      pushDebug(
        "ui",
        "classify:result",
        `classified=${successData.classified_courses.length} unknown=${successData.unknown_count}`,
      );
    } catch (requestError) {
      console.error("Classify request failed", requestError);
      pushDebug("ui", "classify:error", "Could not classify course titles.");
      setError("Could not classify course titles.");
    } finally {
      setIsClassifying(false);
      pushDebug("ui", "classify:done", "classification request completed");
    }
  }

  async function resolveUnknown(course: ClassifiedCourse) {
    if (!course.unknown_queue_id) return;
    const subject = resolveSubjectById[course.unknown_queue_id] ?? "other_units";
    pushDebug("ui", "resolve:start", `unknown_id=${course.unknown_queue_id} subject=${subject}`);
    try {
      const res = await fetch(`${API_BASE}/units/unknowns/${course.unknown_queue_id}/resolve?debug=true`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subject,
          note: "Resolved from transcript review UI",
          create_mapping: true,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        const message = formatApiError(data?.detail, "Could not resolve unknown course.");
        pushDebug("ui", "resolve:error", message);
        setError(message);
        return;
      }
      pushBackendDebug(data?.debug, "units_unknown_resolve");
      pushDebug("ui", "resolve:done", `unknown_id=${course.unknown_queue_id} resolved`);
      await classifyTitles();
    } catch (requestError) {
      console.error("Resolve unknown failed", requestError);
      pushDebug("ui", "resolve:error", "Could not resolve unknown course.");
      setError("Could not resolve unknown course.");
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.headerCard}>
        <h1 className={styles.title}>Transcript Review Workbench</h1>
        <p className={styles.subtitle}>
          Upload one transcript, review GPA extraction, and classify all courses into approved, review, or disproved.
        </p>
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

        <div className={styles.controlsRow}>
          <label className={styles.control}>
            School ID
            <input
              value={schoolId}
              onChange={(e) => setSchoolId(e.target.value)}
              placeholder="optional-school-code"
            />
          </label>
          <button
            className={styles.primaryBtn}
            onClick={() => void classifyTitles()}
            disabled={isClassifying || parsedCourseTitles.length === 0}
          >
            {isClassifying ? "Classifying..." : "Reclassify Courses"}
          </button>
        </div>

        {selectedName && <p className={styles.meta}>Selected: {selectedName}</p>}
        {isAnalyzing && <p className={styles.meta}>Analyzing transcript...</p>}
        {error && <p className={styles.error}>{error}</p>}
      </section>

      <section className={styles.gridTwo}>
        <article className={styles.card}>
          <h2>GPA Summary</h2>
          <div className={styles.statGrid}>
            <div className={styles.statTile}>
              <span>Weighted GPA</span>
              <strong>{analyzeData?.analysis?.gpa?.reported_weighted ?? "N/A"}</strong>
            </div>
            <div className={styles.statTile}>
              <span>Unweighted (4.0)</span>
              <strong>{analyzeData?.analysis?.gpa?.unweighted_4_scale ?? "N/A"}</strong>
            </div>
            <div className={styles.statTile}>
              <span>Scale Detected</span>
              <strong>{analyzeData?.analysis?.gpa?.scale_detected ?? "N/A"}</strong>
            </div>
            <div className={styles.statTile}>
              <span>Method</span>
              <strong>{analyzeData?.analysis?.gpa?.method ?? "N/A"}</strong>
            </div>
          </div>
          <p className={styles.meta}>
            AI configured: {String(analyzeData?.ai_provider?.configured ?? false)} | AI error:{" "}
            {analyzeData?.analysis_error ?? "none"}
          </p>
        </article>

        <article className={styles.card}>
          <h2>Review Status Totals</h2>
          <div className={styles.statGrid}>
            <div className={styles.statTile}>
              <span>Approved</span>
              <strong>{statusCounts.approved}</strong>
            </div>
            <div className={styles.statTile}>
              <span>Review</span>
              <strong>{statusCounts.review}</strong>
            </div>
            <div className={styles.statTile}>
              <span>Disproved</span>
              <strong>{statusCounts.disproved}</strong>
            </div>
            <div className={styles.statTile}>
              <span>Unknown Queue</span>
              <strong>{classifyData?.unknown_count ?? 0}</strong>
            </div>
          </div>
          <h3 className={styles.sectionLabel}>Units by Subject</h3>
          <ul className={styles.unitList}>
            {Object.entries(classifyData?.unit_counts ?? {}).map(([subject, value]) => (
              <li key={subject}>
                <span>{subject}</span>
                <strong>{value}</strong>
              </li>
            ))}
          </ul>
        </article>
      </section>

      <section className={styles.card}>
        <h2>Detected Course Titles</h2>
        <textarea
          className={styles.textarea}
          value={courseTitleText}
          onChange={(e) => setCourseTitleText(e.target.value)}
          placeholder="One course title per line"
        />
      </section>

      <section className={styles.card}>
        <h2>Course Classification Table</h2>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Course</th>
                <th>Category</th>
                <th>Status</th>
                <th>Method</th>
                <th>Confidence</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {(classifyData?.classified_courses ?? []).map((course, index) => {
                const status = inferReviewStatus(course.method);
                return (
                  <tr key={`${course.raw_title}-${index}`}>
                    <td>{course.raw_title}</td>
                    <td>{course.subject ?? "In review"}</td>
                    <td>
                      <span className={`${styles.badge} ${styles[status]}`}>{status}</span>
                    </td>
                    <td>{course.method}</td>
                    <td>{course.confidence.toFixed(2)}</td>
                    <td>
                      {course.unknown_queue_id ? (
                        <div className={styles.resolveBox}>
                          <select
                            value={resolveSubjectById[course.unknown_queue_id] ?? "other_units"}
                            onChange={(e) =>
                              setResolveSubjectById((prev) => ({
                                ...prev,
                                [course.unknown_queue_id as number]: e.target.value,
                              }))
                            }
                          >
                            {SUBJECTS.map((subject) => (
                              <option key={subject} value={subject}>
                                {subject}
                              </option>
                            ))}
                          </select>
                          <button className={styles.secondaryBtn} onClick={() => void resolveUnknown(course)}>
                            Resolve
                          </button>
                        </div>
                      ) : (
                        <span className={styles.meta}>No action</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className={styles.card}>
        <h2>Debug Timeline</h2>
        <p className={styles.meta}>Each event shows source, step label, and detail for tracing the exact flow.</p>
        <div className={styles.debugWrap}>
          <table className={styles.debugTable}>
            <thead>
              <tr>
                <th>Time</th>
                <th>Source</th>
                <th>Step</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {debugEvents.map((event, index) => (
                <tr key={`${event.time}-${index}`}>
                  <td>{event.time}</td>
                  <td>{event.source}</td>
                  <td>{event.step}</td>
                  <td>{event.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {pdfUrl && (
        <section className={styles.card}>
          <h2>PDF Preview</h2>
          <iframe title="PDF preview" src={pdfUrl} className={styles.preview} allowFullScreen />
        </section>
      )}
    </main>
  );
}
