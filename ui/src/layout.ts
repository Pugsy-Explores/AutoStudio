/**
 * Phase 12 — Layout algorithm using dagre.
 *
 * Converts ExecutionGraph to React Flow nodes/edges with proper positioning.
 * Uses dagre for hierarchical layout (not random positions).
 */

import dagre from 'dagre';
import { Node, Edge, Position } from '@xyflow/react';
import { ExecutionGraph, GraphNode, GraphEdge as ExecutionGraphEdge } from './types';

const NODE_WIDTH = 180;
const NODE_HEIGHT = 60;

export function layoutGraph(graph: ExecutionGraph): { nodes: Node[]; edges: Edge[] } {
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));
  dagreGraph.setGraph({ rankdir: 'TB', nodesep: 50, ranksep: 80 });

  graph.nodes.forEach((node) => {
    dagreGraph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  });

  graph.edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });

  dagre.layout(dagreGraph);

  const nodes: Node[] = graph.nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id);
    return {
      id: node.id,
      type: 'executionNode',
      position: {
        x: nodeWithPosition.x - NODE_WIDTH / 2,
        y: nodeWithPosition.y - NODE_HEIGHT / 2,
      },
      data: { node },
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
    };
  });

  const edges: Edge[] = graph.edges.map((edge, idx) => ({
    id: `e-${idx}`,
    source: edge.source,
    target: edge.target,
    type: edge.type === 'retry' ? 'smoothstep' : 'default',
    animated: edge.type === 'retry' || edge.type === 'replan',
    label: edge.type === 'replan' ? 'replan' : undefined,
    style: {
      stroke: edge.type === 'replan' ? '#dc2626' : edge.type === 'retry' ? '#f59e0b' : '#6b7280',
      strokeWidth: edge.type === 'next' ? 2 : 3,
    },
  }));

  return { nodes, edges };
}
