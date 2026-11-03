# RC-07 Heat rejection, free cooling, Dutch climate

**Feeds:** GM-06 HeatRejection, GM-07 FreeCooling, TL-02b climate, DB-34.
**Verification status:** KNMI dry-bulb normals and NOAA design point verified on the web 31 Oct 2026; RH is an assumption `[A]` (replace with KNMI normals if obtained); wet-bulb computed.

## Climate (De Bilt normals 1991–2020)
| | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T_db °C (KNMI) |
3.6 | 3.9 | 6.5 | 9.9 | 13.4 | 16.1 | 18.2 | 17.8 | 14.7 | 10.9 | 7.0 | 4.2 |
| RH % [A] | 87 | 84 | 80 | 75 | 74 | 76 | 77 | 78 | 82 | 85 | 88 | 88 |
| T_wb °C (Stull) | 2.4 | 2.4 | 4.6 | 7.3 | 10.6 | 13.3 | 15.4 | 15.2 | 12.7 | 9.3 | 5.8 | 3.0 |

- Daily series 2027: piecewise-linear interpolation between mid-month values (day 15 of each month; wrap Dec→Jan).
- Design summer point: 1 % cooling DB 25.0 °C, mean coincident WB 18.4 °C (NOAA Engineering Weather Data, Amsterdam WMO 062400, 1973–96). Warm-climate sensitivity: 30 °C DB / 21 °C WB [A].
- Stull (2011) wet-bulb (T °C, RH %): `T_wb = T·atan(0.151977·(RH + 8.313659)^0.5) + atan(T + RH) − atan(RH − 1.676331) + 0.00391838·RH^1.5·atan(0.023101·RH) − 4.686035` (valid RH 5–99 %, T −20…50 °C).

## Heat rejection (cooling tower / dry cooler)
- `Q_reject = Q_cool (1 + 1/COP)` for a chiller (Guidebook p.262):
2.43 × Q_cool at COP 0.7 vs 1.33 at COP 3.
- Cooling tower leaving water: `T_cw,out = T_wb + approach`, approach 4–5 K [A]; range ≈ 5 K (Guidebook rating ΔT 9 °F).
- Fan + cooling-water pump: ≈ 2 % of heat rejected (Guidebook 0.077 kW/ton incremental, p.269) — default 2 % [A].
- Dry cooler: approach 5 K to dry-bulb (RTES paper); fan power ≈ 3× wet tower; cost ≈ 3×, area ≈ 3× (DiPippo Ch23 Miravalles comparison).
- Water consumption MAY (evaporation ≈ Q_reject / h_fg).

## Free cooling (water-side economiser)
- Available when `T_wb + approach_tower + approach_HEX ≤ T_chilled,supply` ⇒ T_wb ≤ 20 − 4.5 − 2 ≈ 13.5 °C.
- Partial free cooling (pre-cooling the chiller return) when T_wb + 6.5 < T_return (26 °C).
- Power = fan + pumps (≈ 2 % of heat moved). Seasonal performance factor of free cooling 24–53 (papers/ Stockholm ATES-GSHP).
- Screening [D]: monthly means make Apr, May, Jun (marginal), Sep, Oct eligible ⇒ ≈ 12.2 of 23.7 GWh cooling; daily simulation decides.

## Validation tests
- Stull: T = 20 °C, RH = 50 % ⇒ T_wb = 13.7 °C (±0.1).
- Monthly table above reproduced from inputs (±0.1 °C).
- Interpolated daily series has 365 values, mean ≈ 10.5 °C.
