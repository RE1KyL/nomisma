export function NomismaEmptyState() {
  return <div className="n-empty-state">
    <img src={`${import.meta.env.BASE_URL}brand/nomisma-symbol.png`} alt="NOMISMA" className="n-empty-logo"/>
    <h2>NOMISMA</h2>
    <p>From Data to Discovery</p>
  </div>;
}
