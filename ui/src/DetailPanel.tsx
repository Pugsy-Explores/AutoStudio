/**
 * Phase 12 — Node detail panel (Step 6).
 *
 * Shows input, output, error, duration, metadata on node selection.
 */

import React from 'react';
import { GraphNode } from './types';

interface DetailPanelProps {
  node: GraphNode | null;
  onClose: () => void;
}

export const DetailPanel: React.FC<DetailPanelProps> = ({ node, onClose }) => {
  if (!node) return null;

  return (
    <div
      style={{
        position: 'absolute',
        top: '20px',
        right: '20px',
        width: '400px',
        maxHeight: 'calc(100vh - 40px)',
        background: '#ffffff',
        borderRadius: '8px',
        boxShadow: '0 4px 24px rgba(0,0,0,0.15)',
        padding: '20px',
        overflow: 'auto',
        zIndex: 10,
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

      {node.input && <Section title="Input" content={JSON.stringify(node.input, null, 2)} code />}
      {node.output && <Section title="Output" content={JSON.stringify(node.output, null, 2)} code />}
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
