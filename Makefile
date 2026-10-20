# Author: Swayam Shakya
.PHONY: check test smoke benchmark gui clean

check:
	python3 -m py_compile *.py scripts/*.py tests/*.py

test:
	python3 -m unittest discover -s tests -v

smoke:
	python3 scripts/smoke_test.py

benchmark:
	python3 scripts/benchmark.py --megabytes 4 --piece-kib 256

gui:
	python3 gui.py

clean:
	rm -rf __pycache__ tests/__pycache__ scripts/__pycache__ downloads
