/**
 * Phase 12 — Main App component.
 *
 * Fetches execution graph from API or uses sample data.
 */

import React, { useState, useEffect } from 'react';
import { ExecutionGraphViewer } from './ExecutionGraphViewer';
import { ExecutionGraph } from './types';

const SAMPLE_GRAPH: ExecutionGraph = {
  trace_id: 'sample_trace_001',
  nodes: [
    {
      id: 's1',
      type: 'step',
      label: 'search',
      status: 'success',
      output: { target: 'find symbol' },
      metadata: { duration_ms: 120, plan_step_index: 1, action: 'search' },
    },
    {
      id: 's2',
      type: 'step',
      label: 'open_file',
      status: 'success',
      output: { target: 'main.py' },
      metadata: { duration_ms: 50, plan_step_index: 2, action: 'open_file' },
    },
    {
      id: 's2_retry',
      type: 'event',
      label: 'retry (1x)',
      status: 'retry',
      metadata: { retry_count: 1, parent_step_id: 's3' },
    },
    {
      id: 's3',
      type: 'step',
      label: 'edit',
      status: 'success',
      output: { target: 'main.py' },
      metadata: { duration_ms: 250, plan_step_index: 3, action: 'edit', attempts: 2 },
    },
  ],
  edges: [
    { source: 's1', target: 's2', type: 'next' },
    { source: 's2', target: 's2_retry', type: 'next' },
    { source: 's2_retry', target: 's3', type: 'retry' },
  ],
};

export const App: React.FC = () => {
  const [graph, setGraph] = useState<ExecutionGraph>(SAMPLE_GRAPH);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const traceId = urlParams.get('trace_id');

    if (traceId) {
      setLoading(true);
      fetch(`/api/graph/${traceId}`)
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((data) => {
          setGraph(data.graph);
          setLoading(false);
        })
        .catch((err) => {
          setError(`Failed to fetch graph: ${err.message}`);
          setLoading(false);
        });
    }
  }, []);

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <p>Loading graph...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <div>
          <p style={{ color: '#dc2626', fontWeight: 600 }}>Error</p>
          <p>{error}</p>
          <p style={{ marginTop: '12px', fontSize: '14px', color: '#6b7280' }}>
            Showing sample graph instead.
          </p>
          <button onClick={() => { setError(null); setGraph(SAMPLE_GRAPH); }}>
            View Sample
          </button>
        </div>
      </div>
    );
  }

  return <ExecutionGraphViewer graph={graph} />;
};
