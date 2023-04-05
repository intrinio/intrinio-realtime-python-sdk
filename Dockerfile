FROM python:3.6

RUN mkdir /intrinio

WORKDIR /intrinio

COPY . /intrinio

RUN pip uninstall 'websocket'
RUN pip install 'websocket-client'
RUN pip install 'requests'
RUN pip install 'wsaccel'

CMD python example_app.py