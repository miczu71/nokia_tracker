FROM python:3.12-alpine

ENV LANG=C.UTF-8

RUN apk add --no-cache jq tzdata

# E10 (docs/PLAN_E10_dokumenty.md), Etap 4: WeasyPrint dla dokumentów
# dowodowych PDF. Biblioteki podane jako so:<nazwa> (dostawca sonamu), nie
# nazwy pakietów — przeżywa przemianowania pakietów w Alpine (pułapka
# ttf-dejavu -> font-dejavu). Bez cairo: WeasyPrint >=53 pisze PDF przez
# pydyf, cairo byłoby martwym ciężarem. font-dejavu pokrywa polskie znaki
# diakrytyczne (ą ć ę ł ń ó ś ź ż) — zweryfikowane w tests/test_exports_pdf.py.
RUN apk add --no-cache \
    so:libgobject-2.0.so.0 so:libpango-1.0.so.0 so:libharfbuzz.so.0 \
    so:libharfbuzz-subset.so.0 so:libfontconfig.so.1 so:libpangoft2-1.0.so.0 \
    font-dejavu

WORKDIR /app

COPY requirements.txt requirements-pdf.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt
# --only-binary=:all: sprawia, że brak gotowego koła (np. na armv7) wywala
# ten krok NATYCHMIAST zamiast kompilować pod QEMU 30-60 minut — `|| echo`
# łapie to i buduje dalej bez PDF (exports/pdf.py::available() == False,
# eksport HTML działa zawsze). Nigdy nie maskować INNYCH awarii pip-a tym
# samym trikiem — to świadomy wyjątek wyłącznie dla tego jednego kroku.
RUN pip3 install --no-cache-dir --only-binary=:all: -r requirements-pdf.txt \
    || echo "PDF: brak kół binarnych dla tej architektury — eksport PDF wyłączony, HTML działa"

COPY nokia_tracker/ ./nokia_tracker/
COPY run.sh /
RUN chmod +x /run.sh

CMD ["/run.sh"]
