/**
 * Phase 12 — Node detail panel (Step 6).
 * Phase 13 — Collapsible prompt/output + copy for LLM debugging.
 */

import React, { useState, useCallback } from 'react';
import { GraphNode } from './types';

interface DetailPanelProps {
  node: GraphNode | null;
  onClose: () => void;
}

export const DetailPanel: React.FC<DetailPanelProps> = ({ node, onClose }) => {
  if (!node) return null;

  const isLlm = node.type === 'llm';

  return (
    <div
      style={{
        position: 'absolute',
        top: '20px',
        right: '20px',
        width: '400px',
        maxHeight: 'calc(100vh - 40px)',
        background: isLlm ? '#faf5ff' : '#ffffff',
        borderRadius: '8px',
        boxShadow: '0 4px 24px rgba(0,0,0,0.15)',
        padding: '20px',
        overflow: 'auto',
        zIndex: 10,
        border: isLlm ? '1px solid #e9d5ff' : undefined,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 700 }}>{node.label}</h2>
        <button
          onClick={onClose}
          style={{
            border: 'none',
            background: 'none',
            fontSize: '20px',
            cursor: 'pointer',
            padding: '4px',
          }}
        >
          ✕
        </button>
      </div>

      <Section title="ID" content={node.id} />
      <Section title="Type" content={node.type} />
      <Section title="Status" content={node.status} />

      {isLlm && node.metadata?.task_name != null && (
        <Section title="Task name" content={String(node.metadata.task_name)} />
      )}
      {isLlm && node.metadata?.model != null && (
        <Section title="Model" content={String(node.metadata.model)} />
      )}
      {node.metadata?.duration_ms != null && (
        <Section title="Latency (ms)" content={String(node.metadata.duration_ms)} />
      )}

      {isLlm && node.input && (
        <CollapsibleJsonSection
          title="Prompt (trimmed)"
          data={node.input}
          copyLabel="Copy prompt block"
          extractCopyText={(d) => {
            const sys = d.system_prompt ? `SYSTEM:\n${d.system_prompt}\n\n` : '';
            const pr = d.prompt != null ? String(d.prompt) : '';
            return sys + (pr ? `USER:\n${pr}` : '');
          }}
        />
      )}
      {isLlm && node.output && (
        <CollapsibleJsonSection
          title="Output (trimmed)"
          data={node.output}
          copyLabel="Copy output"
          extractCopyText={(d) => (d.text != null ? String(d.text) : JSON.stringify(d, null, 2))}
        />
      )}

      {!isLlm && node.input && Object.keys(node.input).length > 0 && (
        <Section title="Input" content={JSON.stringify(node.input, null, 2)} code />
      )}
      {!isLlm && node.output && <Section title="Output" content={JSON.stringify(node.output, null, 2)} code />}
      {node.error && <Section title="Error" content={node.error} error />}

      <Section title="Metadata" content={JSON.stringify(node.metadata, null, 2)} code />
    </div>
  );
};

interface SectionProps {
  title: string;
  content: string;
  code?: boolean;
  error?: boolean;
}

const Section: React.FC<SectionProps> = ({ title, content, code, error }) => (
  <div style={{ marginBottom: '16px' }}>
    <div style={{ fontSize: '12px', fontWeight: 600, color: '#6b7280', marginBottom: '6px' }}>
      {title}
    </div>
    <div
      style={{
        fontSize: '13px',
        color: error ? '#dc2626' : '#1f2937',
        background: code ? '#f9fafb' : 'transparent',
        padding: code ? '8px' : '0',
        borderRadius: code ? '4px' : '0',
        fontFamily: code ? 'monospace' : 'inherit',
        whiteSpace: code ? 'pre-wrap' : 'normal',
        wordBreak: 'break-word',
      }}
    >
      {content}
    </div>
  </div>
);

interface CollapsibleJsonSectionProps {
  title: string;
  data: Record<string, unknown>;
  copyLabel: string;
  extractCopyText: (data: Record<string, unknown>) => string;
}

const CollapsibleJsonSection: React.FC<CollapsibleJsonSectionProps> = ({
  title,
  data,
  copyLabel,
  extractCopyText,
}) => {
  const [open, setOpen] = useState(true);
  const text = JSON.stringify(data, null, 2);
  const copyPayload = extractCopyText(data);

  const onCopy = useCallback(() => {
    void navigator.clipboard.writeText(copyPayload);
  }, [copyPayload]);

  return (
    <div style={{ marginBottom: '16px' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '6px',
          gap: '8px',
        }}
      >
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          style={{
            border: 'none',
            background: 'none',
            padding: 0,
            cursor: 'pointer',
            fontSize: '12px',
            fontWeight: 600,
            color: '#6b7280',
            textAlign: 'left',
            flex: 1,
          }}
        >
          {open ? '▼' : '▶'} {title}
        </button>
        <button
          type="button"
          onClick={onCopy}
          style={{
            fontSize: '11px',
            padding: '4px 8px',
            borderRadius: '4px',
            border: '1px solid #c4b5fd',
            background: '#fff',
            cursor: 'pointer',
            color: '#5b21b6',
            whiteSpace: 'nowrap',
          }}
        >
          {copyLabel}
        </button>
      </div>
      {open && (
        <div
          style={{
            fontSize: '12px',
            color: '#1f2937',
            background: '#f9fafb',
            padding: '8px',
            borderRadius: '4px',
            fontFamily: 'monospace',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: '240px',
            overflow: 'auto',
          }}
        >
          {text}
        </div>
      )}
    </div>
  );
};
