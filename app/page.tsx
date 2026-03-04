"use client"

import React, { useEffect, useState } from "react";

export default function App() {
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    };
  }, [pdfUrl]);

  function handleFile(file?: File | null) {
    if (!file) return;
    if (file.type !== "application/pdf") return; // ignore non-pdf
    if (pdfUrl) URL.revokeObjectURL(pdfUrl);     // cleanup old
    setPdfUrl(URL.createObjectURL(file));
  }

  return (
    <div
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        handleFile(e.dataTransfer.files?.[0]);
      }}
      style={{
        minHeight: "100vh",
        padding: 24,
        background: "#0b0f19",
        color: "white",
        fontFamily: "system-ui",
      }}
    >
      <div
        style={{
          border: "2px dashed #666",
          borderRadius: 12,
          padding: 24,
          cursor: "pointer",
          maxWidth: 900,
          margin: "0 auto",
        }}
        onClick={() => document.getElementById("pdf")?.click()}
      >
        <input
          id="pdf"
          type="file"
          accept="application/pdf"
          style={{ display: "none" }}
          onChange={(e) => handleFile(e.target.files?.[0])}
        />
        Drop a PDF here (or click)
      </div>

      {pdfUrl && (
        <iframe
          title="PDF preview"
          src={pdfUrl}
          style={{
            width: "min(900px, 100%)",
            height: 600,
            display: "block",
            margin: "16px auto 0",
            border: "1px solid #333",
            borderRadius: 12,
            background: "#111",
          }}
        />
      )}
    </div>
  );
}