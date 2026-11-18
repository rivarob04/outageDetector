export default function MapPage({ kiosk = false }: { kiosk?: boolean }) {
  void kiosk;
  return (
    <div>
      <h1 className="wide mt-0 text-3xl font-extrabold">Map</h1>
      <p style={{ color: "var(--muted)" }}>The outage map arrives with vantage agents.</p>
    </div>
  );
}
