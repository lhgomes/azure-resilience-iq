/**
 * Chat Service - TypeScript client for LLM-powered chat API
 * Handles communication with backend chat service
 */

export interface SuggestedEdge {
  source: string;
  target: string;
  relationship?: string;
  confidence: number;
  reason?: string;
}

export interface CriticalityInsight {
  node_id: string;
  suggested_score: number;
  reason: string;
  current_score?: number;
}

export interface RemediationStep {
  step: number;
  title: string;
  description: string;
  estimated_time_minutes?: number;
  prerequisites?: string[];
  validation?: string;
}

export interface RemediationGuide {
  title: string;
  steps: RemediationStep[];
  total_effort_hours: number;
  complexity: string;
  requires_downtime: boolean;
  rollback_procedure: string;
  validation_checklist: string[];
}

export interface ChatResponse {
  message: string;
  suggested_edges: SuggestedEdge[];
  resources_to_highlight: string[];
  criticality_insights: CriticalityInsight[];
  recommendations: any[];
  remediation_guide?: RemediationGuide;
  terraform_code?: string;
  terraform_validation?: string;
  clarifying_questions: string[];
  raw_llm_output?: any;
}

export interface ChatBaselineResponse {
  subscription_id: string;
  summary: string;
  references: Array<{ id: string; label?: string }>;
  reference_count: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  response?: ChatResponse;
}

export interface ChatContext {
  selected_resource_id?: string;
  selected_recommendation_id?: string;
  tab?: 'overview' | 'findings' | 'graph' | 'workloads';
  [key: string]: any;
}

export class LLMChatService {
  private subscriptionId: string;

  constructor(subscriptionId: string) {
    this.subscriptionId = subscriptionId;
  }

  /**
   * Send message to chat API
   */
  async sendMessage(
    message: string,
    context?: ChatContext,
    conversationHistory?: Array<{ role: 'user' | 'assistant'; content: string }>
  ): Promise<ChatResponse> {
    try {
      const response = await fetch(
        `/api/subscriptions/${this.subscriptionId}/chat`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            message,
            subscription_id: this.subscriptionId,
            context,
            conversation_history: conversationHistory,
          }),
        }
      );

      if (!response.ok) {
        const error = await response.text();
        throw new Error(`Chat API error (${response.status}): ${error}`);
      }

      return (await response.json()) as ChatResponse;
    } catch (error) {
      console.error('Chat service error:', error);
      throw error;
    }
  }

  /**
   * Apply a suggested action from chat
   */
  async applyAction(
    actionType: 'add_edge' | 'update_criticality',
    payload: any
  ): Promise<{ status: string; message: string }> {
    try {
      const params = new URLSearchParams({
        action_type: actionType,
        payload: JSON.stringify(payload),
      });

      const response = await fetch(
        `/api/subscriptions/${this.subscriptionId}/chat/action?${params}`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
        }
      );

      if (!response.ok) {
        throw new Error(`Action failed: ${response.statusText}`);
      }

      return (await response.json()) as { status: string; message: string };
    } catch (error) {
      console.error('Chat action error:', error);
      throw error;
    }
  }

  /**
   * Check if chat service is healthy
   */
  async healthCheck(): Promise<boolean> {
    try {
      const response = await fetch(
        `/api/subscriptions/${this.subscriptionId}/chat/health`
      );
      return response.ok;
    } catch {
      return false;
    }
  }

  /**
   * Fetch baseline summary derived from latest LLM annotations
   */
  async getBaselineSummary(): Promise<ChatBaselineResponse> {
    const response = await fetch(
      `/api/subscriptions/${this.subscriptionId}/chat/baseline`
    );

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Baseline API error (${response.status}): ${error}`);
    }

    return (await response.json()) as ChatBaselineResponse;
  }
}

/**
 * Factory for creating chat service instances
 */
export function createChatService(subscriptionId: string): LLMChatService {
  return new LLMChatService(subscriptionId);
}
