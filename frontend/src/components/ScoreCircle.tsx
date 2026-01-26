import React from 'react';
import { formatScore, getScoreColor, getScoreStatus } from '../api/resilience';

interface ScoreCircleProps {
  score: number; // 0.0-1.0
  size?: number;
  showLabel?: boolean;
  label?: string;
}

/**
 * Circular score indicator showing resilience score from 0-100%
 */
const ScoreCircle: React.FC<ScoreCircleProps> = ({
  score,
  size = 120,
  showLabel = true,
  label = 'Resiliency Score'
}) => {
  const radius = (size / 2) - 10;
  const circumference = 2 * Math.PI * radius;
  const scorePercent = score * 100;
  const offset = circumference - (score * circumference);
  const color = getScoreColor(score);
  const status = getScoreStatus(score);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        {/* Background circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke="#e5e7eb"
          strokeWidth="8"
          fill="none"
        />
        {/* Score arc */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke={color}
          strokeWidth="8"
          fill="none"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.5s ease' }}
        />
        {/* Center text */}
        <text
          x={size / 2}
          y={size / 2}
          textAnchor="middle"
          dominantBaseline="middle"
          style={{
            transform: 'rotate(90deg)',
            transformOrigin: 'center',
            fontSize: size * 0.25,
            fontWeight: 'bold',
            fill: color
          }}
        >
          {scorePercent.toFixed(0)}%
        </text>
      </svg>
      {showLabel && (
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '12px', fontWeight: '600', color: '#374151' }}>
            {label}
          </div>
          <div style={{ fontSize: '11px', color: color, fontWeight: '500' }}>
            {status}
          </div>
        </div>
      )}
    </div>
  );
};

export default ScoreCircle;
