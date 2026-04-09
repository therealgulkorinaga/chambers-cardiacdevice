"""Plotly Dash application — real-time simulation dashboard."""

from __future__ import annotations

import json
from typing import Any

try:
    import dash
    from dash import html, dcc, callback, Input, Output, State
    import dash_bootstrap_components as dbc
    import plotly.graph_objects as go
    HAS_DASH = True
except ImportError:
    HAS_DASH = False

from src.orchestrator import SimulationOrchestrator, SimulationConfig


def create_app(orchestrator: SimulationOrchestrator | None = None) -> Any:
    """Create the Dash application."""
    if not HAS_DASH:
        raise ImportError("Dash not installed. Run: pip install chamber-sentinel-cied-sim[viz]")

    app = dash.Dash(
        __name__,
        external_stylesheets=[dbc.themes.DARKLY],
        title="Chamber Sentinel CIED Simulator",
        suppress_callback_exceptions=True,
    )

    # Layout
    app.layout = dbc.Container([
        # Header
        dbc.Row([
            dbc.Col([
                html.H2("CHAMBER SENTINEL CIED SIMULATOR", className="text-light mb-0"),
                html.P("Burn-by-Default vs Persist-by-Default Architecture Comparison",
                       className="text-muted"),
            ], width=8),
            dbc.Col([
                dbc.Select(
                    id="scenario-select",
                    options=[
                        {"label": "Baseline Single Patient", "value": "baseline"},
                        {"label": "AF Detection & Alert", "value": "af_detection"},
                        {"label": "Lead Fracture Adverse Event", "value": "lead_fracture"},
                        {"label": "Population 1000 Mixed", "value": "population_1000"},
                    ],
                    value="baseline",
                    className="mb-2",
                ),
                dbc.Button("Run Simulation", id="run-btn", color="success", className="w-100"),
            ], width=4),
        ], className="py-3 border-bottom mb-3"),

        # Status bar
        dbc.Row([
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.H6("Sim Clock", className="text-muted mb-0"),
                    html.H4(id="sim-clock", children="Day 0"),
                ])
            ]), width=2),
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.H6("Patients", className="text-muted mb-0"),
                    html.H4(id="patient-count", children="0"),
                ])
            ]), width=2),
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.H6("Status", className="text-muted mb-0"),
                    html.H4(id="sim-status", children="Ready"),
                ])
            ]), width=2),
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.H6("Events", className="text-muted mb-0"),
                    html.H4(id="event-count", children="0"),
                ])
            ]), width=2),
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.H6("Total Burns", className="text-muted mb-0"),
                    html.H4(id="burn-count", children="0"),
                ])
            ]), width=2),
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.H6("Persistence Ratio", className="text-muted mb-0"),
                    html.H4(id="persistence-ratio", children="--"),
                ])
            ]), width=2),
        ], className="mb-3"),

        # Main comparison panels
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader("CURRENT ARCHITECTURE", className="bg-danger text-white"),
                    dbc.CardBody([
                        html.P(id="current-bytes", children="Data Persisted: 0 MB"),
                        html.P(id="current-growth", children="Growth: 0 MB/day"),
                        html.P(id="current-records", children="Records: 0"),
                        html.P("Retention: Indefinite", className="text-muted"),
                    ]),
                ]),
            ], width=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader("CHAMBERS ARCHITECTURE", className="bg-success text-white"),
                    dbc.CardBody([
                        html.P(id="chambers-bytes", children="Data Persisted: 0 MB"),
                        html.P(id="chambers-relay", children="Relay: 0 items"),
                        html.P(id="chambers-burns", children="Burns Today: 0"),
                        html.P("Relay TTL: 72 hours", className="text-muted"),
                    ]),
                ]),
            ], width=6),
        ], className="mb-3"),

        # Charts
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader("Persistence Volume Over Time"),
                    dbc.CardBody([
                        dcc.Graph(id="persistence-chart", style={"height": "350px"}),
                    ]),
                ]),
            ], width=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader("Burn Events Timeline"),
                    dbc.CardBody([
                        dcc.Graph(id="burn-chart", style={"height": "350px"}),
                    ]),
                ]),
            ], width=6),
        ], className="mb-3"),

        # World status
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader("Typed Worlds Status"),
                    dbc.CardBody(id="worlds-status"),
                ]),
            ], width=12),
        ]),

        # Auto-refresh interval
        dcc.Interval(id="refresh-interval", interval=2000, disabled=True),

        # Hidden store for simulation state
        dcc.Store(id="sim-state", data={}),

    ], fluid=True, className="bg-dark text-light min-vh-100")

    # Callbacks
    @callback(
        Output("sim-state", "data"),
        Output("refresh-interval", "disabled"),
        Input("run-btn", "n_clicks"),
        State("scenario-select", "value"),
        prevent_initial_call=True,
    )
    def start_simulation(n_clicks, scenario):
        if not n_clicks:
            return dash.no_update, True

        config = SimulationConfig(
            duration_days=30 if scenario == "baseline" else 180,
            cohort_size=1 if scenario != "population_1000" else 100,
            time_step_s=3600.0,
            random_seed=42,
        )
        orch = SimulationOrchestrator(config)
        orch.initialize()
        stats = orch.run()

        return {
            "stats": _serialize_stats(stats),
            "snapshots": [_serialize_snapshot(s) for s in orch.time_series],
            "comparison": _serialize_stats(orch.get_comparison_snapshot()),
        }, True  # Disable interval after completion

    @callback(
        [
            Output("sim-clock", "children"),
            Output("patient-count", "children"),
            Output("sim-status", "children"),
            Output("event-count", "children"),
            Output("burn-count", "children"),
            Output("persistence-ratio", "children"),
            Output("current-bytes", "children"),
            Output("current-growth", "children"),
            Output("current-records", "children"),
            Output("chambers-bytes", "children"),
            Output("chambers-relay", "children"),
            Output("chambers-burns", "children"),
            Output("persistence-chart", "figure"),
            Output("burn-chart", "figure"),
            Output("worlds-status", "children"),
        ],
        Input("sim-state", "data"),
    )
    def update_display(data):
        if not data or "stats" not in data:
            empty_fig = go.Figure()
            empty_fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            return (
                "Day 0", "0", "Ready", "0", "0", "--",
                "Data Persisted: 0 MB", "Growth: 0 MB/day", "Records: 0",
                "Data Persisted: 0 MB", "Relay: 0 items", "Burns Today: 0",
                empty_fig, empty_fig, "No data yet",
            )

        stats = data["stats"]
        snapshots = data.get("snapshots", [])
        comparison = data.get("comparison", {})

        clock = stats.get("clock", {})
        day = int(clock.get("sim_days", 0))
        patients = stats.get("patients", 0)
        status = stats.get("status", "unknown")
        events = stats.get("total_events", 0)
        burns = stats.get("total_burns", 0)

        current = stats.get("current_arch", {})
        current_mb = current.get("total_mb", 0)
        current_records = current.get("total_records", 0)

        chambers = stats.get("chambers_arch", {}).get("relay", {})
        relay_items = chambers.get("items_in_relay", 0)

        persistence = comparison.get("persistence", {})
        ratio = persistence.get("ratio", 0)
        chambers_mb = persistence.get("chambers_mb", 0)

        # Persistence chart
        persistence_fig = go.Figure()
        if snapshots:
            days_list = [s.get("day", 0) for s in snapshots]
            current_series = [s.get("current_arch", {}).get("total_bytes", 0) / (1024*1024) for s in snapshots]
            chambers_series = [s.get("chambers_arch", {}).get("total_bytes", 0) / (1024*1024) for s in snapshots]

            persistence_fig.add_trace(go.Scatter(x=days_list, y=current_series, name="Current Arch", line=dict(color="#ff6b6b")))
            persistence_fig.add_trace(go.Scatter(x=days_list, y=chambers_series, name="Chambers Arch", line=dict(color="#51cf66")))
            persistence_fig.update_layout(
                template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="Day", yaxis_title="Data Volume (MB)", legend=dict(x=0.01, y=0.99),
                margin=dict(l=40, r=20, t=20, b=40),
            )

        # Burn chart
        burn_fig = go.Figure()
        if snapshots:
            burn_series = [s.get("chambers_arch", {}).get("total_burns", 0) for s in snapshots]
            burn_fig.add_trace(go.Bar(x=days_list, y=burn_series, name="Cumulative Burns", marker_color="#51cf66"))
            burn_fig.update_layout(
                template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="Day", yaxis_title="Cumulative Burns",
                margin=dict(l=40, r=20, t=20, b=40),
            )

        # Worlds status
        worlds = stats.get("chambers_arch", {}).get("worlds", {})
        world_rows = []
        for name, ws in worlds.items():
            world_rows.append(
                dbc.Row([
                    dbc.Col(html.Strong(name.replace("_", " ").title()), width=3),
                    dbc.Col(f"Accepted: {ws.get('total_accepted', 0):,}", width=2),
                    dbc.Col(f"Burned: {ws.get('total_burned', 0):,}", width=2),
                    dbc.Col(f"Active: {ws.get('active_records', 0):,}", width=2),
                    dbc.Col(f"{ws.get('total_mb', 0):.2f} MB", width=2),
                    dbc.Col(f"Holds: {ws.get('active_holds', 0)}", width=1),
                ], className="mb-1")
            )
        worlds_content = html.Div(world_rows) if world_rows else "No data"

        return (
            f"Day {day}",
            str(patients),
            status.title(),
            f"{events:,}",
            f"{burns:,}",
            f"{ratio:.1f}x" if ratio > 0 else "--",
            f"Data Persisted: {current_mb:.2f} MB",
            f"Growth: {current_mb / max(day, 1):.2f} MB/day",
            f"Records: {current_records:,}",
            f"Data Persisted: {chambers_mb:.2f} MB",
            f"Relay: {relay_items} items",
            f"Total Burns: {burns:,}",
            persistence_fig,
            burn_fig,
            worlds_content,
        )

    return app


def _serialize_stats(stats: dict) -> dict:
    """Make stats JSON-serializable."""
    result = {}
    for k, v in stats.items():
        if isinstance(v, dict):
            result[k] = _serialize_stats(v)
        elif isinstance(v, (list, tuple)):
            result[k] = [_serialize_stats(i) if isinstance(i, dict) else i for i in v]
        elif isinstance(v, float) and (v != v):  # NaN check
            result[k] = 0.0
        elif isinstance(v, set):
            result[k] = list(v)
        else:
            try:
                json.dumps(v)
                result[k] = v
            except (TypeError, ValueError):
                result[k] = str(v)
    return result


_serialize_snapshot = _serialize_stats


def run_app(host: str = "0.0.0.0", port: int = 8050, debug: bool = False) -> None:
    """Run the dashboard application."""
    app = create_app()
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_app(debug=True)
