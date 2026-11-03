"""Transforma as falhas do relatório JUnit do pytest em anotações do GitHub Actions."""
import sys
import xml.etree.ElementTree as ET

for case in ET.parse(sys.argv[1]).getroot().iter("testcase"):
    problem = case.find("failure")
    if problem is None:
        problem = case.find("error")
    if problem is None:
        continue
    # Só o final do erro: é onde fica a linha que falhou. Quebras de linha viram %0A no formato do Actions.
    text = (problem.text or problem.get("message") or "")[-900:].replace("%", "%25").replace("\n", "%0A")
    print(f"::error title={case.get('name')}::{text}")
