/**
 * Phase 12 — Custom execution node component.
 *
 * Displays step/llm/event nodes with status-based styling.
 * Implements Phase 12 Step 7 (status colors).
 */

import React from 'react';
import { Handle, Position } from '@xyflow/react';
import { GraphNode } from './types';

interface ExecutionNodeProps {
  data: {
    node: GraphNode;
  };
  selected: boolean;
}

const STATUS_COLORS = {
  success: '#d4edda',
  failure: '#f8d7da',
  retry: '#fff3cd',
  pending: '#e2e8f0',
};

const STATUS_BORDER_COLORS = {
  success: '#28a745',
  failure: '#dc3545',
  retry: '#ffc107',
  pending: '#94a3b8',
};

const LLM_BG = '#ede9fe';
const LLM_BORDER = '#7c3aed';

export const ExecutionNode: React.FC<ExecutionNodeProps> = ({ data, selected }) => {
  const { node } = data;
  const isLlm = node.type === 'llm';
  let bgColor = STATUS_COLORS[node.status as keyof typeof STATUS_COLORS] || '#ffffff';
  let borderColor = STATUS_BORDER_COLORS[node.status as keyof typeof STATUS_BORDER_COLORS] || '#000000';
  if (isLlm) {
    bgColor = LLM_BG;
    borderColor = LLM_BORDER;
  }

  return (
    <div
      style={{
        padding: '12px 16px',
        borderRadius: '6px',
        border: `2px solid ${selected ? '#3b82f6' : borderColor}`,
        background: bgColor,
        minWidth: '160px',
        boxShadow: selected ? '0 4px 12px rgba(0,0,0,0.15)' : '0 2px 4px rgba(0,0,0,0.1)',
        transition: 'all 0.2s ease',
      }}
    >
      <Handle type="target" position={Position.Top} />
      
      <div style={{ fontSize: '11px', color: '#6b7280', marginBottom: '4px', fontWeight: 500 }}>
        {isLlm ? '🧠 LLM' : node.type.toUpperCase()}
      </div>
      
      <div style={{ fontSize: '14px', fontWeight: 600, color: '#1f2937', marginBottom: '4px' }}>
        {node.label}
      </div>
      
      {node.metadata.duration_ms !== undefined && (
        <div style={{ fontSize: '11px', color: '#6b7280' }}>
          {node.metadata.duration_ms}ms
        </div>
      )}
      
      {node.error && (
        <div style={{ fontSize: '10px', color: '#dc2626', marginTop: '4px', fontWeight: 500 }}>
          ⚠ Error
        </div>
      )}
      
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
};
