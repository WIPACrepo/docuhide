FROM python:2.7.18

RUN apt-get update && apt-get install -y python-ldap3 && apt-get clean

ENV PYTHONPATH=/usr/lib/python2.7/dist-packages