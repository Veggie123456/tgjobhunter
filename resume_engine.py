import json, os, re
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from openai import OpenAI

ROOT = Path(__file__).parent
PROFILE = json.loads((ROOT / "profile.json").read_text())
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6")

SCHEMA = {
  "type": "object",
  "properties": {
    "headline": {"type":"string"},
    "summary": {"type":"string"},
    "skills": {"type":"array","items":{"type":"string"}},
    "roles": {"type":"array","items":{
      "type":"object",
      "properties":{
        "company":{"type":"string"},"title":{"type":"string"},"dates":{"type":"string"},
        "bullets":{"type":"array","items":{"type":"string"}}
      },
      "required":["company","title","dates","bullets"],"additionalProperties":False
    }}
  },
  "required":["headline","summary","skills","roles"],"additionalProperties":False
}

def tailor(job):
    client = OpenAI()
    instructions = """You tailor Daniil Shurik's resume. Use ONLY facts in the supplied master profile.
Do not invent metrics, employers, software, certifications, domain experience, or responsibilities.
Select and reframe truthful experience for the target job. Keep bullets concrete and concise.
Normally use 5-7 roles and 3-5 bullets for the most relevant current roles, 2-3 for older roles.
Follow every resume_rules item. Return structured JSON only."""
    response = client.responses.create(
        model=MODEL,
        instructions=instructions,
        input="MASTER PROFILE:\n"+json.dumps(PROFILE)+"\n\nTARGET JOB:\n"+json.dumps(job),
        text={"format":{"type":"json_schema","name":"tailored_resume","strict":True,"schema":SCHEMA}}
    )
    return json.loads(response.output_text)

def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+","_",s).strip("_")[:80] or "resume"

def build_pdf(data, output_path):
    styles=getSampleStyleSheet()
    name=ParagraphStyle("name",parent=styles["Heading1"],fontName="Helvetica-Bold",fontSize=20,leading=22,alignment=TA_CENTER,spaceAfter=3)
    head=ParagraphStyle("head",parent=styles["Normal"],fontName="Helvetica-Bold",fontSize=9.5,leading=11,alignment=TA_CENTER,textColor=colors.HexColor("#333333"),spaceAfter=4)
    contact=ParagraphStyle("contact",parent=styles["Normal"],fontSize=8.5,leading=10,alignment=TA_CENTER,textColor=colors.HexColor("#444444"),spaceAfter=8)
    sec=ParagraphStyle("sec",parent=styles["Heading2"],fontName="Helvetica-Bold",fontSize=10.5,leading=12,spaceBefore=6,spaceAfter=4,textColor=colors.HexColor("#222222"))
    body=ParagraphStyle("body",parent=styles["BodyText"],fontSize=8.6,leading=11,spaceAfter=3)
    bullet=ParagraphStyle("bullet",parent=body,leftIndent=10,firstLineIndent=-6,spaceAfter=2)
    small=ParagraphStyle("small",parent=body,fontSize=8,leading=10)
    doc=SimpleDocTemplate(str(output_path),pagesize=LETTER,rightMargin=.48*inch,leftMargin=.48*inch,topMargin=.42*inch,bottomMargin=.42*inch)
    story=[
      Paragraph(PROFILE["identity"]["name"].upper(),name),
      Paragraph(data["headline"].upper(),head),
      Paragraph(f'{PROFILE["identity"]["phone"]} &nbsp; | &nbsp; {PROFILE["identity"]["email"]} &nbsp; | &nbsp; {PROFILE["identity"]["location"]}',contact),
      Paragraph("PROFESSIONAL PROFILE",sec), Paragraph(data["summary"],body),
      Paragraph("CORE SKILLS",sec), Paragraph(" • ".join(data["skills"][:16]),small),
      Paragraph("PROFESSIONAL EXPERIENCE",sec)
    ]
    for role in data["roles"]:
        block=[Paragraph(f'<b>{role["title"]}</b>',body),Paragraph(f'{role["company"]} | {role["dates"]}',small)]
        for b in role["bullets"]: block.append(Paragraph("• "+b,bullet))
        block.append(Spacer(1,3))
        story.append(KeepTogether(block))
    story += [
      Paragraph("EDUCATION",sec), Paragraph(PROFILE["identity"]["education"],body),
      Paragraph("TECHNOLOGY",sec), Paragraph(" • ".join(PROFILE["tools"]),small),
      Paragraph("LANGUAGES",sec), Paragraph(" • ".join(PROFILE["identity"]["languages"]),small)
    ]
    doc.build(story)

def generate_resume(job, out_dir):
    data=tailor(job)
    path=Path(out_dir)/"Daniil Shurik Resume.pdf"
    build_pdf(data,path)
    return path, data
