function AirportList({ airports }) {
    return (
        <ul>
            {airports.map(airport => (
                <li key={airport.Origin}>
                    {airport.Origin} — inheritance rate: {airport.inheritance_rate.toFixed(3)}
                </li>
            ))}
        </ul>
    )
}

export default AirportList