import React from 'react';
import { CategoryBreakdown, formatScore, getScoreColor } from '../api/resilience';

interface CategoryBreakdownProps {
  breakdown: CategoryBreakdown;
  showWeights?: boolean;
}

/**
 * Display category-level scores with visual bars
 */
const CategoryBreakdownView: React.FC<CategoryBreakdownProps> = ({
  breakdown,
  showWeights = false
}) => {
  const categories = Object.entries(breakdown).sort((a, b) => b[1].score - a[1].score);

  if (categories.length === 0) {
    return (
      <div style={{ padding: '16px', color: '#6b7280', textAlign: 'center' }}>
        No category data available
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', padding: '8px' }}>
      {categories.map(([categoryName, data]) => {
        const score = data.score;
        const scorePercent = score * 100;
        const color = getScoreColor(score);

        return (
          <div key={categoryName} style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '13px', fontWeight: '500', color: '#374151' }}>
                  {categoryName}
                </span>
                {showWeights && (
                  <span style={{ fontSize: '11px', color: '#9ca3af' }}>
                    (weight: {(data.weight * 100).toFixed(0)}%)
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '11px', color: '#6b7280' }}>
                  {data.passed_count}/{data.checks_count}
                </span>
                <span style={{ fontSize: '13px', fontWeight: '600', color: color }}>
                  {scorePercent.toFixed(0)}%
                </span>
              </div>
            </div>
            <div style={{ 
              width: '100%', 
              height: '6px', 
              backgroundColor: '#e5e7eb', 
              borderRadius: '3px',
              overflow: 'hidden'
            }}>
              <div
                style={{
                  width: `${scorePercent}%`,
                  height: '100%',
                  backgroundColor: color,
                  transition: 'width 0.5s ease',
                  borderRadius: '3px'
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default CategoryBreakdownView;
