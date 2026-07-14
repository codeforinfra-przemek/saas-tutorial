# Initial Setup:
```
python3 -m venv venv
source venv/bin/activate
cd src/
pwd
/home/prz/code/saas-tutorial/src
pip install -r ../requirements.txt 
django-admin startproject saashome
python manage.py runserver
python manage.py startapp visits
python manage.py makemigrations visits
python manage.py migrate
python manage.py collectstatic
```