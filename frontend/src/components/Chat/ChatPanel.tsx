import React, { useState, useEffect, useRef, ReactNode, ChangeEvent, KeyboardEvent } from 'react';
import {
  LLMChatService,
  ChatMessage as ChatMessageType,
  ChatResponse,
  ChatContext,
  SuggestedEdge,
} from '../../services/chatService';
import './ChatPanel.css';

interface MentionableResource {
  id: string;
  label?: string;
}

export interface ChatPanelProps {
  subscriptionId: string;
  context?: ChatContext;
  onResourceHighlight?: (resourceIds: string[]) => void;
  onEdgeSuggest?: (edges: SuggestedEdge[]) => void;
  onRecommendationSelect?: (recommendationId: string, recommendationTitle?: string) => void;
  height?: number;
  isMinimized?: boolean;
  mode?: 'floating' | 'embedded';
  refreshToken?: number;
  showBadgeWhenClosed?: boolean;
  showCloseButton?: boolean;
  getResourceLabel?: (resourceId: string) => string | undefined;
  mentionableResources?: MentionableResource[];
}

export const ChatPanel: React.FC<ChatPanelProps> = ({
  subscriptionId = '',
  context,
  onResourceHighlight,
  onEdgeSuggest,
  onRecommendationSelect,
  height = 500,
  isMinimized = false,
  mode = 'floating',
  refreshToken = 0,
  showBadgeWhenClosed = false,
  showCloseButton = false,
  getResourceLabel,
  mentionableResources = [],
}: ChatPanelProps): ReactNode => {
  const [messages, setMessages] = useState<ChatMessageType[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(!isMinimized);
  const [error, setError] = useState<string | null>(null);
  const [lastBaselineAt, setLastBaselineAt] = useState<Date | null>(null);
  const [relativeClock, setRelativeClock] = useState(0);
  const [baselineSummary, setBaselineSummary] = useState<string>('');
  const [baselineReferences, setBaselineReferences] = useState<Array<{ id: string; label?: string }>>([]);
  const [isBaselineOpen, setIsBaselineOpen] = useState(false);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState('');
  const chatService = useRef<LLMChatService | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const lastSubscriptionIdRef = useRef<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const [mentionQuery, setMentionQuery] = useState<string>('');
  const [mentionStart, setMentionStart] = useState<number | null>(null);
  const [isMentionOpen, setIsMentionOpen] = useState(false);
  const [activeMentionIndex, setActiveMentionIndex] = useState(0);
  const [mentionTokenMap, setMentionTokenMap] = useState<Record<string, string>>({});

  const buildWelcomeMessage = (baselineSummary?: string): ChatMessageType => {
    const cleaned = (baselineSummary || '').trim();
    const hasBaseline = cleaned.length > 0 && cleaned !== 'No prior LLM annotations available.';
    const content = hasBaseline
      ? `Baseline analysis from the latest refresh:\n${cleaned}\n\nAsk me anything about this workload.`
      : 'Hello! I can help you understand your infrastructure, analyze issues, and guide you through fixes. What would you like to know?';

    return {
      id: hasBaseline ? `baseline-${Date.now()}` : 'welcome',
      role: 'assistant',
      content,
      timestamp: new Date(),
    };
  };

  const buildBaselineRefreshMessage = (): ChatMessageType => {
    const content = 'Baseline refreshed from the latest annotations.';

    return {
      id: `baseline-refresh-${Date.now()}`,
      role: 'assistant',
      content,
      timestamp: new Date(),
    };
  };

  const formatBaselineTime = (value: Date): string => {
    const seconds = Math.floor((Date.now() - value.getTime()) / 1000);
    if (seconds < 10) return 'just now';
    if (seconds < 60) return `${seconds} seconds ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
    const days = Math.floor(hours / 24);
    return `${days} day${days === 1 ? '' : 's'} ago`;
  };

  const resolveResourceLabel = (id: string, fallback?: string): string => {
    return fallback || getResourceLabel?.(id) || id;
  };

  const resolveRecommendationId = (recommendation: any): string => {
    const candidate = recommendation?.recommendation_id || recommendation?.id || '';
    return typeof candidate === 'string' ? candidate.trim() : String(candidate || '').trim();
  };

  const resolveRecommendationTitle = (recommendation: any): string => {
    const candidate = recommendation?.title || recommendation?.description || recommendation?.recommendation_id || recommendation?.id || 'Recommendation';
    return typeof candidate === 'string' ? candidate.trim() : String(candidate || 'Recommendation').trim();
  };

  const renderResourceChips = (items: Array<{ id: string; label?: string }>): ReactNode => {
    return items.map((item: { id: string; label?: string }) => (
      <button
        key={item.id}
        className="chat-resource-chip"
        onClick={(): void => onResourceHighlight?.([item.id])}
        type="button"
        title={item.id}
      >
        {resolveResourceLabel(item.id, item.label)}
      </button>
    ));
  };

  useEffect(() => {
    if (!lastBaselineAt) return;
    const timer = window.setInterval(() => setRelativeClock(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, [lastBaselineAt]);

  const renderMessageContent = (content: string): ReactNode => {
    const lines = content.split(/\r?\n/);
    return lines.map((line: string, lineIndex: number) => {
      const parts = line.split('**');
      const spans = parts.map((part: string, idx: number) => {
        if (idx % 2 === 1) {
          // Bold text
          return <strong key={`b-${lineIndex}-${idx}`}>{part}</strong>;
        }
        // Regular text - detect and make resource IDs clickable
        return renderTextWithResourceLinks(part, lineIndex, idx);
      });
      return (
        <span key={`l-${lineIndex}`}>
          {spans}
          {lineIndex < lines.length - 1 ? <br /> : null}
        </span>
      );
    });
  };

  const renderTextWithResourceLinks = (text: string, lineIndex: number, partIndex: number): ReactNode => {
    // Detect Azure resource IDs in the format: /subscriptions/{subscriptionId}/...
    const resourceIdPattern = /\/subscriptions\/[a-f0-9\-]+(?:\/resourceGroups\/[a-zA-Z0-9\-_\.]+)?(?:\/providers\/[a-zA-Z0-9\.]+\/[a-zA-Z0-9]+\/[^\/\s]+)?/g;
    const parts: (string | ReactNode)[] = [];
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    const matches: Array<{ id: string; start: number; end: number }> = [];
    while ((match = resourceIdPattern.exec(text)) !== null) {
      const resourceId = match[0];
      matches.push({
        id: resourceId,
        start: match.index,
        end: match.index + resourceId.length,
      });
    }

    if (matches.length === 0) {
      return text;
    }

    matches.forEach((match, idx) => {
      // Add text before this match
      if (match.start > lastIndex) {
        parts.push(text.slice(lastIndex, match.start));
      }

      // Add clickable resource link
      parts.push(
        <button
          key={`res-${lineIndex}-${partIndex}-${idx}`}
          className="chat-inline-resource"
          onClick={() => onResourceHighlight?.([match.id])}
          type="button"
          title={match.id}
        >
          {resolveResourceLabel(match.id)}
        </button>
      );

      lastIndex = match.end;
    });

    // Add remaining text
    if (lastIndex < text.length) {
      parts.push(text.slice(lastIndex));
    }

    return <span key={`text-${lineIndex}-${partIndex}`}>{parts}</span>;
  };

  // Initialize chat service
  useEffect(() => {
    chatService.current = new LLMChatService(subscriptionId);

    // Check health
    chatService.current.healthCheck().then((healthy: boolean) => {
      if (!healthy) {
        setError('Chat service is not available');
      }
    });
  }, [subscriptionId]);

  // Load baseline summary after refresh or subscription change
  useEffect(() => {
    let isActive = true;

    const loadBaseline = async (): Promise<void> => {
      if (!chatService.current || !subscriptionId) return;
      const isNewSubscription = lastSubscriptionIdRef.current !== subscriptionId;
      lastSubscriptionIdRef.current = subscriptionId;
      try {
        const baseline = await chatService.current.getBaselineSummary();
        if (!isActive) return;
        setBaselineSummary(baseline.summary || '');
        setBaselineReferences(baseline.references || []);
        setIsBaselineOpen(false);
        const nextMessage = isNewSubscription
          ? buildWelcomeMessage()
          : buildBaselineRefreshMessage();
        setMessages((prev: ChatMessageType[]) => {
          if (isNewSubscription || prev.length === 0) {
            return [nextMessage];
          }
          // For baseline refresh, replace the last message if it's also a baseline refresh
          // This prevents duplicates when refreshToken changes rapidly
          const lastMsg = prev[prev.length - 1];
          if (lastMsg?.id?.startsWith('baseline-refresh-')) {
            return [...prev.slice(0, -1), nextMessage];
          }
          return [...prev, nextMessage];
        });
        setLastBaselineAt(new Date());
      } catch {
        if (!isActive) return;
        if (isNewSubscription) {
          setMessages([buildWelcomeMessage()]);
        }
      }
    };

    loadBaseline();
    return () => {
      isActive = false;
    };
  }, [subscriptionId, refreshToken]);

  // Auto-scroll to latest message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const getResourceShortName = (resourceId: string): string => {
    const normalized = String(resourceId || '').replace(/\/+$/, '');
    const segments = normalized.split('/').filter(Boolean);
    return segments.length > 0 ? segments[segments.length - 1] : resourceId;
  };

  const getResourceGroupName = (resourceId: string): string => {
    const match = String(resourceId || '').match(/\/resourcegroups\/([^/]+)/i);
    if (!match || !match[1]) return 'N/A';
    return match[1];
  };

  const extractMentionTokens = (text: string): string[] => {
    const matches = text.match(/#([^\s#]+)/g) || [];
    return matches
      .map((match: string) => match.slice(1).replace(/[.,;:!?]+$/, '').trim())
      .filter((token: string) => token.length > 0);
  };

  const resolveMentionTokenToResourceId = (token: string): string | null => {
    const normalizedToken = token.trim().toLowerCase();
    if (!normalizedToken) return null;

    if (normalizedToken.startsWith('/subscriptions/')) {
      return token.startsWith('/') ? token : `/${token}`;
    }

    const mapped = mentionTokenMap[normalizedToken];
    if (mapped) {
      return mapped;
    }

    const exactMatches = mentionableResources.filter((resource: MentionableResource) => {
      const shortName = getResourceShortName(resource.id).toLowerCase();
      const label = resolveResourceLabel(resource.id, resource.label).toLowerCase();
      return shortName === normalizedToken || label === normalizedToken;
    });
    if (exactMatches.length === 1) {
      return exactMatches[0].id;
    }

    const idMatches = mentionableResources.filter((resource: MentionableResource) =>
      resource.id.toLowerCase().endsWith(`/${normalizedToken}`)
    );
    if (idMatches.length === 1) {
      return idMatches[0].id;
    }

    return null;
  };

  const extractReferencedResourceIds = (text: string): string[] => {
    const mentionTokens = extractMentionTokens(text);
    const ids: string[] = [];

    mentionTokens.forEach((token: string) => {
      const resolvedId = resolveMentionTokenToResourceId(token);
      if (resolvedId) ids.push(resolvedId);
    });

    const seen = new Set<string>();
    const deduped: string[] = [];
    ids.forEach((resourceId: string) => {
      const key = resourceId.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      deduped.push(resourceId);
    });
    return deduped;
  };

  const filteredMentionResources = mentionableResources
    .filter((item: MentionableResource) => {
      if (!mentionQuery.trim()) return true;
      const queryLower = mentionQuery.trim().toLowerCase();
      const label = resolveResourceLabel(item.id, item.label).toLowerCase();
      return item.id.toLowerCase().includes(queryLower) || label.includes(queryLower);
    })
    .slice(0, 8);

  const updateMentionState = (value: string, caretPosition: number): void => {
    const beforeCaret = value.slice(0, caretPosition);
    const match = beforeCaret.match(/(?:^|\s)#([^#\s]*)$/);
    if (!match) {
      setIsMentionOpen(false);
      setMentionQuery('');
      setMentionStart(null);
      setActiveMentionIndex(0);
      return;
    }

    const tokenStart = beforeCaret.lastIndexOf('#');
    setMentionStart(tokenStart);
    setMentionQuery(match[1] || '');
    setIsMentionOpen(true);
    setActiveMentionIndex(0);
  };

  const applyMentionSelection = (resource: MentionableResource): void => {
    if (mentionStart === null || !inputRef.current) return;

    const textarea = inputRef.current;
    const selectionStart = textarea.selectionStart ?? input.length;
    const shortName = getResourceShortName(resource.id);
    const mentionToken = `#${shortName} `;
    const nextValue = `${input.slice(0, mentionStart)}${mentionToken}${input.slice(selectionStart)}`;
    const nextCaret = mentionStart + mentionToken.length;

    setInput(nextValue);
    setMentionTokenMap((prev: Record<string, string>) => ({
      ...prev,
      [shortName.toLowerCase()]: resource.id,
    }));
    setIsMentionOpen(false);
    setMentionQuery('');
    setMentionStart(null);
    setActiveMentionIndex(0);

    window.requestAnimationFrame(() => {
      textarea.focus();
      textarea.setSelectionRange(nextCaret, nextCaret);
    });
  };

  const handleSendMessage = async (): Promise<void> => {
    if (!input.trim() || isLoading || !chatService.current) return;

    const currentInput = input;
    const referencedResourceIds = extractReferencedResourceIds(currentInput);

    const userMessage: ChatMessageType = {
      id: `msg-${Date.now()}`,
      role: 'user',
      content: currentInput,
      timestamp: new Date(),
    };

    setMessages((prev: ChatMessageType[]) => [...prev, userMessage]);
    setInput('');
    setMentionTokenMap({});
    setIsMentionOpen(false);
    setMentionQuery('');
    setMentionStart(null);
    setIsLoading(true);
    setError(null);

    try {
      // Build conversation history for context
      const conversationHistory = messages
        .filter((m: ChatMessageType) => m.role === 'user' || m.role === 'assistant')
        .map((m: ChatMessageType) => ({
          role: m.role,
          content: m.content,
        }));

      const response = await chatService.current.sendMessage(
        currentInput,
        context,
        conversationHistory,
        referencedResourceIds
      );

      const assistantMessage: ChatMessageType = {
        id: `msg-${Date.now()}`,
        role: 'assistant',
        content: response.message,
        timestamp: new Date(),
        response,
      };

      setMessages((prev: ChatMessageType[]) => [...prev, assistantMessage]);

      // Handle highlights
      if (response.resources_to_highlight && onResourceHighlight) {
        onResourceHighlight(response.resources_to_highlight);
      }

      // Handle edge suggestions
      if (response.suggested_edges && onEdgeSuggest) {
        onEdgeSuggest(response.suggested_edges);
      }
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : 'An error occurred';
      setError(errorMessage);
      setMessages((prev: ChatMessageType[]) => [
        ...prev,
        {
          id: `error-${Date.now()}`,
          role: 'assistant',
          content: `Sorry, something went wrong: ${errorMessage}`,
          timestamp: new Date(),
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (isMentionOpen && filteredMentionResources.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveMentionIndex((prev: number) => (prev + 1) % filteredMentionResources.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveMentionIndex((prev: number) =>
          prev === 0 ? filteredMentionResources.length - 1 : prev - 1
        );
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setIsMentionOpen(false);
        return;
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        applyMentionSelection(filteredMentionResources[activeMentionIndex]);
        return;
      }
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const handleTextChange = (e: ChangeEvent<HTMLTextAreaElement>): void => {
    const value = e.target.value;
    setInput(value);
    setMentionTokenMap((prev: Record<string, string>) => {
      const activeTokens = new Set(extractMentionTokens(value).map((token: string) => token.toLowerCase()));
      const next: Record<string, string> = {};
      Object.entries(prev).forEach(([token, resourceId]) => {
        if (activeTokens.has(token)) {
          next[token] = resourceId;
        }
      });
      return next;
    });
    updateMentionState(value, e.target.selectionStart ?? value.length);
  };

  const handleCopyCode = async (code: string): Promise<void> => {
    try {
      await navigator.clipboard.writeText(code);
    } catch (err) {
      console.error('Failed to copy code', err);
    }
  };

  const handleEditMessage = (messageId: string, content: string): void => {
    setEditingMessageId(messageId);
    setEditingText(content);
  };

  const handleCancelEdit = (): void => {
    setEditingMessageId(null);
    setEditingText('');
  };

  const handleResendMessage = async (): Promise<void> => {
    if (!editingText.trim() || isLoading) return;

    // Remove the old message (user and response)
    const messageIndex = messages.findIndex(m => m.id === editingMessageId);
    if (messageIndex === -1) return;

    // Remove the user message and any assistant response after it
    const newMessages = messages.slice(0, messageIndex);
    setMessages(newMessages);
    setEditingMessageId(null);
    setEditingText('');

    // Send the edited message as if it's new
    setInput(editingText);

    // Wait a tick for state to update, then send
    setTimeout(() => {
      void handleSendMessage();
    }, 0);
  };

  const handleRetryMessage = async (messageId: string): Promise<void> => {
    if (isLoading) return;

    // Find the user message that failed
    const messageIndex = messages.findIndex(m => m.id === messageId);
    if (messageIndex === -1) return;

    const userMessage = messages[messageIndex];
    if (userMessage.role !== 'user') return;

    // Remove the failed message and any error response
    const newMessages = messages.slice(0, messageIndex);
    setMessages(newMessages);

    // Resend the same message
    setInput(userMessage.content);

    // Wait a tick for state to update, then send
    setTimeout(() => {
      void handleSendMessage();
    }, 0);
  };

  if (!isOpen && (mode === 'floating' || showBadgeWhenClosed)) {
    return (
      <button
        className={`chat-toggle ${mode === 'embedded' ? 'chat-toggle-embedded' : ''}`}
        onClick={(): void => setIsOpen(true)}
        title="Open chat assistant"
      >
        <img src="/copilot-logo.png" alt="Chat assistant" className="chat-toggle-icon" />
      </button>
    );
  }

  if (!isOpen && mode === 'embedded') {
    return null;
  }

  return (
    <div className={`chat-panel ${mode}`} style={{ height: mode === 'floating' ? `${height}px` : '100%' }}>
      {(mode === 'floating' || showCloseButton) && (
        <div className="chat-header">
          <h3>Assistant</h3>
          <button
            className="chat-close"
            onClick={(): void => setIsOpen(false)}
            title="Minimize chat"
          >
            ✕
          </button>
        </div>
      )}

      {lastBaselineAt && (
        <div className="chat-baseline-badge">
          Baseline refreshed {formatBaselineTime(lastBaselineAt)}
        </div>
      )}

      {(baselineSummary || baselineReferences.length > 0) && (
        <div className="chat-baseline">
          <button
            className="chat-baseline-toggle"
            type="button"
            onClick={(): void => setIsBaselineOpen((prev: boolean) => !prev)}
          >
            Used {baselineReferences.length} reference{baselineReferences.length === 1 ? '' : 's'}
          </button>
          {isBaselineOpen && (
            <div className="chat-baseline-body">
              {baselineSummary && (
                <div className="chat-baseline-summary">
                  {renderMessageContent(baselineSummary)}
                </div>
              )}
              {baselineReferences.length > 0 && (
                <div className="chat-baseline-refs">
                  {renderResourceChips(baselineReferences)}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="chat-messages">
        {messages.map((msg: ChatMessageType) => (
          <div key={msg.id} className={`message message-${msg.role}`}>
            <div className="message-bubble">
              {/* Edit mode for user messages */}
              {editingMessageId === msg.id && msg.role === 'user' ? (
                <div className="message-edit-mode">
                  <textarea
                    value={editingText}
                    onChange={(e) => setEditingText(e.target.value)}
                    className="message-edit-textarea"
                    rows={3}
                  />
                  <div className="message-edit-buttons">
                    <button
                      onClick={(): Promise<void> => handleResendMessage()}
                      className="edit-resend-btn"
                      disabled={!editingText.trim() || isLoading}
                    >
                      Resend
                    </button>
                    <button
                      onClick={handleCancelEdit}
                      className="edit-cancel-btn"
                      disabled={isLoading}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="message-content">{renderMessageContent(msg.content)}</div>

                  {msg.role === 'assistant' && msg.response?.metrics && (
                    <div className="message-footnote">
                      ⚙️ {msg.response.metrics.model || msg.response.metrics.provider || 'llm'}
                      {typeof msg.response.metrics.total_tokens === 'number'
                        ? ` • ${msg.response.metrics.total_tokens} tokens`
                        : ''}
                      {typeof msg.response.metrics.total_ms === 'number'
                        ? ` • ${(msg.response.metrics.total_ms / 1000).toFixed(1)}s`
                        : ''}
                    </div>
                  )}

                  {/* Message actions (edit for user messages, retry for failed responses) */}
                  <div className="message-actions">
                    {msg.role === 'user' && (
                      <button
                        className="message-action-btn"
                        onClick={() => handleEditMessage(msg.id, msg.content)}
                        title="Edit and resend"
                        disabled={isLoading}
                      >
                        ✏️ Edit
                      </button>
                    )}
                    {msg.role === 'assistant' && !msg.response && !msg.id?.startsWith('baseline') && !msg.id?.startsWith('welcome') && (
                      <button
                        className="message-action-btn"
                        onClick={() => handleRetryMessage(msg.id)}
                        title="Retry"
                        disabled={isLoading}
                      >
                        🔄 Retry
                      </button>
                    )}
                  </div>
                </>
              )}

              {/* Extended response content */}
              {msg.response && (
              <>
                {msg.response.resources_to_highlight &&
                  msg.response.resources_to_highlight.length > 0 && (
                    <div className="message-highlight">
                      <small>
                        Highlighting {msg.response.resources_to_highlight.length}{' '}
                        resource(s)
                      </small>
                      <div className="chat-inline-refs">
                        {renderResourceChips(
                          msg.response.resources_to_highlight.map((id: string) => ({
                            id,
                            label: getResourceLabel?.(id),
                          }))
                        )}
                      </div>
                    </div>
                  )}

                {msg.response.recommendations &&
                  msg.response.recommendations.length > 0 && (
                    <div className="recommendations-section">
                      <div className="recommendations-header">
                        🎯 {msg.response.recommendations.length} recommendation(s):
                      </div>
                      {msg.response.recommendations.map((rec: any, i: number) => (
                        <div key={i} className="recommendation-item">
                          <button
                            type="button"
                            className="recommendation-link"
                            onClick={() => {
                              const recommendationId = resolveRecommendationId(rec);
                              const recommendationTitle = resolveRecommendationTitle(rec);
                              if (!recommendationId && !recommendationTitle) return;
                              onRecommendationSelect?.(recommendationId, recommendationTitle);
                            }}
                            title="Open in Findings"
                          >
                            {resolveRecommendationTitle(rec)}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                {msg.response.sources && msg.response.sources.length > 0 && (
                  <div className="recommendations-section">
                    <div className="recommendations-header">
                      📚 Sources:
                    </div>
                    {msg.response.sources.map((source, i: number) => (
                      <div key={i} className="recommendation-item">
                        <a
                          href={source.url}
                          target="_blank"
                          rel="noreferrer"
                          className="resource-chip"
                        >
                          {source.title || source.url}
                        </a>
                        {/* {source.type && (
                          <div className="rec-metadata">
                            <span className="effort">Type: {source.type}</span>
                          </div>
                        )} */}
                      </div>
                    ))}
                  </div>
                )}

                {msg.response.clarifying_questions &&
                  msg.response.clarifying_questions.length > 0 && (
                    <div className="clarifying-questions">
                      <p className="question-intro">
                        Before I proceed, I need to understand better:
                      </p>
                      {msg.response.clarifying_questions.map((q: string, i: number) => (
                        <div key={i} className="question">
                          <span className="question-mark">❓</span>
                          <span>{q}</span>
                        </div>
                      ))}
                    </div>
                  )}

                {msg.response.suggested_edges &&
                  msg.response.suggested_edges.length > 0 && (
                    <div className="suggested-edges">
                      <div className="edges-header">
                        💡 {msg.response.suggested_edges.length} suggested
                        connection(s):
                      </div>
                      {msg.response.suggested_edges.map((edge: SuggestedEdge, i: number) => (
                        <div key={i} className="edge-suggestion">
                          <div className="edge-info" title={`${edge.source} → ${edge.target}`}>
                            <strong>{resolveResourceLabel(edge.source)}</strong> → <strong>{resolveResourceLabel(edge.target)}</strong>
                          </div>
                          <div className="edge-details">
                            <span className="relationship">{edge.relationship}</span>
                            <span className="confidence">
                              Confidence: {(edge.confidence * 100).toFixed(0)}%
                            </span>
                          </div>
                          {edge.reason && (
                            <div className="edge-reason">{edge.reason}</div>
                          )}
                          <button
                            className="edge-accept-btn"
                            onClick={() =>
                              handleAcceptEdge(
                                edge.source,
                                edge.target,
                                edge.relationship || 'related'
                              )
                            }
                          >
                            Accept
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                {msg.response.terraform_code && (
                  <>
                    {msg.response.terraform_validation && (
                      <div className="chat-validation-warning">
                        {renderMessageContent(msg.response.terraform_validation)}
                      </div>
                    )}
                    <div className="chat-code-block">
                      <div className="chat-code-header">
                        <span>Terraform</span>
                        <button
                          className="chat-copy-button"
                          onClick={(): void => void handleCopyCode(msg.response?.terraform_code || '')}
                          type="button"
                        >
                          Copy
                        </button>
                      </div>
                      <pre className="chat-code"><code>{msg.response.terraform_code}</code></pre>
                    </div>
                  </>
                )}
              </>
            )}
            </div>{/* end message-bubble */}
          </div>
        ))}

        {isLoading && (
          <div className="message message-assistant">
            <div className="message-bubble">
              <div className="message-content">
                <span className="loading-dots">Analyzing...</span>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {error && <div className="chat-error">{error}</div>}

      <div className="chat-input-area">
        <div className="chat-input-wrapper">
          <div className="chat-composer">
            <textarea
              ref={inputRef}
              value={input}
              onChange={handleTextChange}
              onKeyDown={handleKeyDown}
              placeholder="Ask about your infrastructure, issues, or fixes... Use # to reference a resource."
              disabled={isLoading}
              rows={1}
            />
          </div>

          {isMentionOpen && filteredMentionResources.length > 0 && (
            <div className="chat-mention-menu">
              {filteredMentionResources.map((resource: MentionableResource, index: number) => (
                <button
                  key={resource.id}
                  className={`chat-mention-item ${index === activeMentionIndex ? 'active' : ''}`}
                  type="button"
                  onMouseDown={(event: React.MouseEvent<HTMLButtonElement>) => {
                    event.preventDefault();
                    applyMentionSelection(resource);
                  }}
                  title={resource.id}
                >
                  <span className="chat-mention-label">{resolveResourceLabel(resource.id, resource.label)}</span>
                  <span className="chat-mention-id">RG: {getResourceGroupName(resource.id)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          onClick={(): Promise<void> => handleSendMessage()}
          disabled={!input.trim() || isLoading}
          className="send-button"
          aria-label="Send message"
        >
          <img src="/send-button.png" alt="Send" className="send-button-image" />
        </button>
      </div>

    </div>
  );

  async function handleAcceptEdge(source: string, target: string, relationship: string): Promise<void> {
    if (!chatService.current) return;

    try {
      await chatService.current.applyAction('add_edge', {
        source,
        target,
        relationship,
        confidence: 0.85,
      });

      setMessages((prev) => [
        ...prev,
        {
          id: `msg-${Date.now()}`,
          role: 'assistant',
          content: `Edge added successfully: ${source} → ${target}`,
          timestamp: new Date(),
        },
      ]);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to add edge';
      setError(errorMessage);
    }
  }
};
