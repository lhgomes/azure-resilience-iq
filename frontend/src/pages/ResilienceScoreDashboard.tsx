import React, { useEffect, useState } from 'react';
import { 
  getSubscriptionEvaluation, 
  WorkloadScoring,
  SubscriptionEvaluationResponse 
} from '../api/resilience';
import ScoreCircle from '../components/ScoreCircle';
import CategoryBreakdownView from '../components/CategoryBreakdownView';

interface ResiliencyScoreDashboardProps {
  subscriptionId: string;
}

/**
 * Dashboard displaying workload resilience scoring and category breakdowns
 */
const ResiliencyScoreDashboard: React.FC<ResiliencyScoreDashboardProps> = ({
  subscriptionId
}) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<SubscriptionEvaluationResponse | null>(null);

  useEffect(() => {
    const loadScores = async () => {
      try {
        setLoading(true);
        setError(null);
        const evaluation = await getSubscriptionEvaluation(subscriptionId);
        setData(evaluation);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load resilience scores');
      } finally {
        setLoading(false);
      }
    };

    loadScores();
  }, [subscriptionId]);

  if (loading) {
    return (
      <div style={{ padding: '24px', textAlign: 'center', color: '#6b7280' }}>
        Loading resilience scores...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ 
        padding: '24px', 
        backgroundColor: '#fef2f2', 
        border: '1px solid #fecaca',
        borderRadius: '8px',
        color: '#991b1b'
      }}>
        <strong>Error:</strong> {error}
      </div>
    );
  }

  if (!data || !data.workload_score) {
    return (
      <div style={{ 
        padding: '24px', 
        backgroundColor: '#fefce8', 
        border: '1px solid #fef08a',
        borderRadius: '8px',
        color: '#854d0e'
      }}>
        <strong>No scoring data available.</strong>
        <p style={{ marginTop: '8px', fontSize: '14px' }}>
          Run resilience evaluation first:
          <br />
          <code style={{ backgroundColor: '#fffbeb', padding: '4px 8px', borderRadius: '4px', display: 'inline-block', marginTop: '4px' }}>
            python -m app.resilience.run --subscription-id {subscriptionId}
          </code>
        </p>
      </div>
    );
  }

  const { workload_score, category_breakdown, summary } = data;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', padding: '16px' }}>
      {/* Header with overall score */}
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'space-between',
        padding: '20px',
        backgroundColor: '#ffffff',
        borderRadius: '12px',
        boxShadow: '0 1px 3px rgba(0,0,0,0.1)'
      }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '20px', fontWeight: '600', color: '#111827' }}>
            Workload Resiliency Score
          </h2>
          <p style={{ margin: '8px 0 0 0', fontSize: '14px', color: '#6b7280' }}>
            {summary.total_resources_evaluated} resources evaluated • {summary.total_checks} checks
            {summary.total_failed_checks > 0 && (
              <span style={{ marginLeft: '8px', color: '#ef4444', fontWeight: '500' }}>
                {summary.total_failed_checks} failing
              </span>
            )}
          </p>
        </div>
        <ScoreCircle score={workload_score} size={140} showLabel={true} label="Overall Score" />
      </div>

      {/* Category Breakdown */}
      {category_breakdown && Object.keys(category_breakdown).length > 0 && (
        <div style={{ 
          padding: '20px',
          backgroundColor: '#ffffff',
          borderRadius: '12px',
          boxShadow: '0 1px 3px rgba(0,0,0,0.1)'
        }}>
          <h3 style={{ margin: '0 0 16px 0', fontSize: '16px', fontWeight: '600', color: '#111827' }}>
            Category Scores
          </h3>
          <CategoryBreakdownView breakdown={category_breakdown} showWeights={true} />
        </div>
      )}

      {/* Resource Summary */}
      <div style={{ 
        padding: '20px',
        backgroundColor: '#ffffff',
        borderRadius: '12px',
        boxShadow: '0 1px 3px rgba(0,0,0,0.1)'
      }}>
        <h3 style={{ margin: '0 0 16px 0', fontSize: '16px', fontWeight: '600', color: '#111827' }}>
          Resource Summary
        </h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
          <StatCard 
            label="Total Resources" 
            value={summary.total_resources_evaluated.toString()} 
            color="#3b82f6"
          />
          <StatCard 
            label="Passed" 
            value={summary.total_passed_checks.toString()} 
            color="#22c55e"
          />
          <StatCard 
            label="Failed" 
            value={summary.total_failed_checks.toString()} 
            color="#ef4444"
          />
          <StatCard 
            label="Pass Rate" 
            value={`${((summary.total_passed_checks / summary.total_checks) * 100).toFixed(1)}%`} 
            color="#8b5cf6"
          />
        </div>
      </div>
    </div>
  );
};

interface StatCardProps {
  label: string;
  value: string;
  color: string;
}

const StatCard: React.FC<StatCardProps> = ({ label, value, color }) => (
  <div style={{ 
    padding: '16px',
    backgroundColor: '#f9fafb',
    borderRadius: '8px',
    border: '1px solid #e5e7eb'
  }}>
    <div style={{ fontSize: '12px', color: '#6b7280', marginBottom: '4px' }}>
      {label}
    </div>
    <div style={{ fontSize: '24px', fontWeight: '700', color }}>
      {value}
    </div>
  </div>
);

export default ResiliencyScoreDashboard;
