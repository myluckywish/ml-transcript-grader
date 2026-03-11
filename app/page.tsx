"use client"

import React, { useEffect, useState } from "react";

const PARSER_URL = process.env.NEXT_PUBLIC_PARSER_API_URL ?? "http://127.0.0.1:8000/parse";

type ParseResponse = {
  extracted_text?: string;
  parsed_content?: {
    json?: unknown | null;
  };
  detail?: string;
};

export default function App() {
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [extractedText, setExtractedText] = useState("");
  const [parsedJsonText, setParsedJsonText] = useState("null");
  const [isParsing, setIsParsing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    };
  }, [pdfUrl]);

  async function handleFile(file?: File | null) {
    if (!file) return;

    setSelectedName(file.name);
    setError(null);
    setExtractedText("");
    setParsedJsonText("null");

    const nextPdfUrl = file.type === "application/pdf" ? URL.createObjectURL(file) : null;
    if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    setPdfUrl(nextPdfUrl);

    const payload = new FormData();
    payload.append("file", file);

    setIsParsing(true);
    try {
      const res = await fetch(PARSER_URL, {
        method: "POST",
        body: payload,
      });
      const data: ParseResponse = await res.json();
      if (!res.ok) {
        console.error("Parse API error", {
          status: res.status,
          statusText: res.statusText,
          fileName: file.name,
          detail: data?.detail,
          response: data,
        });
        setError(data?.detail ?? "Failed to parse file.");
        return;
      }
      setExtractedText(data.extracted_text ?? "");
      setParsedJsonText(JSON.stringify(data.parsed_content?.json ?? null, null, 2));
    } catch (err) {
      console.error("Parse request failed", {
        fileName: file.name,
        parserUrl: PARSER_URL,
        error: err,
      });
      setError("Could not reach parser backend. Run npm run dev:stack (or start backend on port 8000).");
    } finally {
      setIsParsing(false);
    }
  }

  return (
    <div style={{ maxWidth: 1000, margin: "2rem auto", padding: "0 1rem", fontFamily: "sans-serif" }}>
      <div
        style={{
          border: "2px dashed #9ca3af",
          borderRadius: 12,
          padding: "1.5rem",
          cursor: "pointer",
          marginBottom: "1rem",
        }}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          void handleFile(e.dataTransfer.files?.[0]);
        }}
        onClick={() => document.getElementById("document-file")?.click()}
      >
        <input
          id="document-file"
          type="file"
          style={{ display: "none" }}
          onChange={(e) => void handleFile(e.target.files?.[0])}
        />
        <strong>Drop a document here (or click to upload)</strong>
        <div style={{ marginTop: 6, fontSize: 14, color: "#4b5563" }}>
          Supports: PDF, TXT, MD, CSV, JSON, DOCX
        </div>
      </div>

      {selectedName && <div style={{ marginBottom: "0.75rem" }}>Selected: {selectedName}</div>}
      {isParsing && <div style={{ marginBottom: "0.75rem" }}>Parsing document...</div>}
      {error && <div style={{ marginBottom: "0.75rem", color: "#b91c1c" }}>{error}</div>}

      {(extractedText || parsedJsonText) && (
        <section style={{ marginBottom: "1.25rem" }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
              gap: "1rem",
            }}
          >
            <div>
              <h2 style={{ fontSize: "1rem", marginBottom: "0.5rem" }}>Parsed JSON</h2>
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  background: "#f8fafc",
                  border: "1px solid #e5e7eb",
                  borderRadius: 12,
                  padding: "1rem",
                  maxHeight: 320,
                  overflow: "auto",
                }}
              >
                {parsedJsonText}
              </pre>
            </div>
            <div>
              <h2 style={{ fontSize: "1rem", marginBottom: "0.5rem" }}>Extracted Text</h2>
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  background: "#f8fafc",
                  border: "1px solid #e5e7eb",
                  borderRadius: 12,
                  padding: "1rem",
                  maxHeight: 320,
                  overflow: "auto",
                }}
              >
                {extractedText}
              </pre>
            </div>
          </div>
        </section>
      )}

      {pdfUrl && (
        <section>
          <h2 style={{ fontSize: "1rem", marginBottom: "0.5rem" }}>PDF Preview</h2>
          <iframe
            title="PDF preview"
            src={pdfUrl}
            width="100%"
            height="700"
            style={{ minHeight: "70vh", border: "1px solid #e5e7eb", borderRadius: 12 }}
            allowFullScreen
          />
        </section>
      )}
    </div>
  );
}
