/**
 * Phase 12 — Execution Graph UI.
 *
 * React Flow visualization with:
 * - Hierarchical layout (dagre)
 * - Status-based node styling
 * - Click-to-drill-down detail panel
 * - Retry and replan edge visualization
 */

import React, { useState, useCallback, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { ExecutionGraph, GraphNode } from './types';
import { layoutGraph } from './layout';
import { ExecutionNode } from './ExecutionNode';
import { DetailPanel } from './DetailPanel';

const nodeTypes = {
  executionNode: ExecutionNode,
};

interface ExecutionGraphViewerProps {
  graph: ExecutionGraph;
}

export const ExecutionGraphViewer: React.FC<ExecutionGraphViewerProps> = ({ graph }) => {
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  const { nodes: layoutNodes, edges: layoutEdges } = useMemo(() => layoutGraph(graph), [graph]);

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layoutEdges);

  const onNodeClick: NodeMouseHandler = useCallback((event, node) => {
    setSelectedNode(node.data.node as GraphNode);
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedNode(null);
  }, []);

  return (
    <div style={{ width: '100vw', height: '100vh', background: '#fafafa' }}>
      <div style={{ position: 'absolute', top: '20px', left: '20px', zIndex: 10 }}>
        <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 700, color: '#1f2937' }}>
          Execution Graph
        </h1>
        <p style={{ margin: '4px 0 0 0', fontSize: '13px', color: '#6b7280' }}>
          Trace: {graph.trace_id}
        </p>
      </div>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.2}
        maxZoom={2}
      >
        <Background />
        <Controls />
        <MiniMap
          nodeColor={(node) => {
            const gn = node.data.node as GraphNode;
            if (gn?.type === 'llm') return '#7c3aed';
            const status = gn?.status;
            if (status === 'success') return '#28a745';
            if (status === 'failure') return '#dc3545';
            if (status === 'retry') return '#ffc107';
            return '#94a3b8';
          }}
        />
      </ReactFlow>

      <DetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
    </div>
  );
};
