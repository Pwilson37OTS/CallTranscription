APP_CSS = """
<style>
:root{
  --brand-navy: #0D2A39;
  --brand-sky:  #36ADEC;
  --brand-coral:#FF6F59;
  --brand-forest:#004638;
  --brand-mint: #C0D7BB;
  --brand-ice:  #BEDCFE;
  --brand-sand: #F0E5D5;
  --text: #0D2A39;
  --muted: rgba(13,42,57,0.65);
  --card: rgba(255,255,255,0.82);
  --border: rgba(13,42,57,0.12);
  --shadow: 0 10px 28px rgba(13,42,57,0.12);
  --radius: 16px;
}

.stApp {
  background:
    radial-gradient(circle at top right, rgba(54,173,236,0.16), transparent 24%),
    radial-gradient(circle at left bottom, rgba(192,215,187,0.18), transparent 22%),
    linear-gradient(180deg, #f8fbfe 0%, #eef5fb 100%);
  color: var(--text);
}

.block-container {
  padding-top: 3rem;
  padding-bottom: 2rem;
}

[data-testid="stSidebar"] {
  background: linear-gradient(180deg, rgba(13,42,57,0.98) 0%, rgba(0,70,56,0.98) 100%);
  border-right: 1px solid rgba(190,220,254,0.18);
}

[data-testid="stSidebar"] * {
  color: white !important;
}

/* Sidebar form fields keep light backgrounds, so force dark text inside them */
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] textarea,
[data-testid="stSidebar"] [data-baseweb="select"] *,
[data-testid="stSidebar"] [data-baseweb="popover"] * {
  color: var(--brand-navy) !important;
}

/* Expanders/forms in the sidebar render on a light card, but the blanket
   white-text rule above makes their labels and header invisible. Restore
   dark, readable text for that content. */
[data-testid="stSidebar"] [data-testid="stExpander"] summary,
[data-testid="stSidebar"] [data-testid="stExpander"] summary p,
[data-testid="stSidebar"] [data-testid="stForm"] label,
[data-testid="stSidebar"] [data-testid="stForm"] label p {
  color: var(--brand-navy) !important;
}

/* Streamlit form submit buttons aren't matched by the .stButton gradient
   rule below, so a sidebar submit button ("Update Password") rendered as a
   pale default button with invisible white text. Give it the brand gradient. */
[data-testid="stSidebar"] [data-testid="stFormSubmitButton"] > button {
  background: linear-gradient(135deg, var(--brand-navy) 0%, var(--brand-sky) 100%) !important;
  color: white !important;
  border: none !important;
  border-radius: 12px;
  font-weight: 600;
}

[data-testid="stHeader"] {
  background: rgba(255,255,255,0.55);
  backdrop-filter: blur(8px);
}

h1, h2, h3 {
  color: var(--brand-navy);
  letter-spacing: -0.02em;
  font-weight: 800;
}

label {
  font-weight: 700 !important;
  color: var(--brand-navy) !important;
  margin-bottom: 4px;
}

.brand-hero {
  background: linear-gradient(135deg, rgba(13,42,57,0.96) 0%, rgba(54,173,236,0.94) 100%);
  border: 1px solid rgba(255,255,255,0.18);
  box-shadow: var(--shadow);
  border-radius: 22px;
  padding: 1.25rem 1.35rem;
  margin-bottom: 1rem;
  color: white;
}

.brand-hero h1 {
  color: white !important;
  margin: 0;
  font-size: 2rem;
}

.brand-hero p {
  margin: 0.35rem 0 0 0;
  color: rgba(255,255,255,0.9);
  font-size: 0.98rem;
}

[data-testid="stHorizontalBlock"] > div,
[data-testid="stVerticalBlock"] > div:has(> [data-testid="stMarkdownContainer"]),
div[data-testid="stForm"] {
  border-radius: var(--radius);
}

[data-testid="stTextInputRoot"],
[data-testid="stTextAreaRoot"],
[data-testid="stSelectbox"],
[data-testid="stFileUploader"] {
  background: rgba(255,255,255,0.72);
  border-radius: 14px;
}

textarea, input, [data-baseweb="select"] > div {
  border-radius: 12px !important;
  background: #f5f9fd !important;
  border: 2px solid rgba(13,42,57,0.18) !important;
}

textarea:focus, input:focus {
  border: 2px solid var(--brand-sky) !important;
  background: white !important;
  box-shadow: 0 0 0 2px rgba(54,173,236,0.15);
}

div[data-testid="stForm"],
[data-testid="stExpander"],
[data-testid="stMetric"],
[data-testid="stAudio"] {
  background: var(--card);
  border: 1px solid var(--border);
  box-shadow: var(--shadow);
  border-radius: var(--radius);
  padding: 0.35rem;
}

.stTabs [data-baseweb="tab-list"] {
  gap: 0.5rem;
}

.stTabs [data-baseweb="tab"] {
  background: rgba(255,255,255,0.7);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 0.5rem 0.9rem;
  color: var(--brand-navy);
}

.stTabs [aria-selected="true"] {
  background: linear-gradient(135deg, var(--brand-navy) 0%, var(--brand-sky) 100%) !important;
  color: white !important;
  border-color: transparent !important;
}

.stButton > button, .stDownloadButton > button {
  background: linear-gradient(135deg, var(--brand-navy) 0%, var(--brand-sky) 100%);
  color: white;
  border: none;
  border-radius: 12px;
  padding: 0.6rem 1rem;
  font-weight: 600;
  box-shadow: 0 8px 20px rgba(13,42,57,0.18);
}

.stButton > button:hover, .stDownloadButton > button:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 22px rgba(13,42,57,0.24);
}

.status-pill {
  display: inline-block;
  padding: 0.28rem 0.7rem;
  border-radius: 999px;
  background: rgba(54,173,236,0.12);
  color: var(--brand-navy);
  border: 1px solid rgba(54,173,236,0.22);
  font-size: 0.85rem;
  font-weight: 700;
  margin-top: 0.2rem;
}
</style>
"""
