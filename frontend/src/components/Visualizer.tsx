interface VisualizerProps {
  active: boolean;
}

// 8 bars; 5 distinct delay offsets, looped so adjacent bars don't sync
const BARS: { width: string; delay: string }[] = [
  { width: "w-1", delay: "0s" },
  { width: "w-1", delay: "-0.15s" },
  { width: "w-0.5", delay: "-0.45s" },
  { width: "w-1", delay: "-0.3s" },
  { width: "w-1", delay: "-0.6s" },
  { width: "w-0.5", delay: "0s" },
  { width: "w-1", delay: "-0.15s" },
  { width: "w-1", delay: "-0.45s" },
];

export default function Visualizer({ active }: VisualizerProps) {
  return (
    <div
      className="flex h-6 w-12 items-end gap-0.5"
      aria-hidden="true"
      title={active ? "Playing" : "Idle"}
    >
      {BARS.map((bar, i) => (
        <span
          key={i}
          className={`viz-bar h-full rounded-sm bg-accent ${bar.width} ${
            active ? "viz-active" : ""
          }`}
          style={{ animationDelay: bar.delay }}
        />
      ))}
    </div>
  );
}
