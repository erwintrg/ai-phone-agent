PYTHON ?= python3

.PHONY: demo test plan render-n8n serve

demo:
	$(PYTHON) demo.py

test:
	$(PYTHON) -m pytest

plan:
	$(PYTHON) -m phone_agent plan

render-n8n:
	$(PYTHON) -m phone_agent render-n8n

serve:
	$(PYTHON) -m phone_agent serve
