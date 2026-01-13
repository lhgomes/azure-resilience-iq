import React from "react";

interface ResilienceCircleProps {
  score: number; // Resilience score 0.0-1.0
  size?: number; // diameter in pixels, default 80
  children?: React.ReactNode; // Icon or other content to display in center
}

/**
 * Renders a circular progress indicator showing resilience score.
 * The circle shows the score percentage in green, with remaining in red.
 */
const ResilienceCircle: React.FC<ResilienceCircleProps> = ({
  score,
  size = 80,
  children,
}) => {
  const passPercentage = score * 100;
  const failPercentage = 100 - passPercentage;

  // Create conic gradient: green for score, red for remaining
  const conicGradient = `conic-gradient(
    from 0deg,
    #10b981 0deg ${(passPercentage / 100) * 360}deg,
    #ef4444 ${(passPercentage / 100) * 360}deg 360deg
  )`;

  const tooltipText = `Resilience Score: ${passPercentage.toFixed(1)}%`;

  return (
    <div
      style={{
        position: "relative",
        width: `${size}px`,
        height: `${size}px`,
        borderRadius: "50%",
        background: conicGradient,
        padding: "4px",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        flexDirection: "column",
      }}
      title={tooltipText}
    >
      {/* Inner white circle to create ring effect */}
      <div
        style={{
          width: "100%",
          height: "100%",
          borderRadius: "50%",
          background: "#ffffff",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          flexDirection: "column",
          position: "relative",
        }}
      >
        {/* Center content (icon) */}
        {children && (
          <div style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
          }}>
            {children}
          </div>
        )}
        
        {/* Pass percentage text at bottom */}
        <span
          style={{
            fontSize: "10px",
            fontWeight: 700,
            color: "#10b981",
            lineHeight: "1",
            letterSpacing: "-0.5px",
            marginTop: "auto",
            paddingBottom: "4px",
          }}
        >
          {passPercentage.toFixed(0)}%
        </span>
      </div>
    </div>
  );
};

export default ResilienceCircle;
