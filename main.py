
import os
import re
from datetime import datetime
from functools import wraps
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-me")

# Настройки PostgreSQL
DB_HOST = "127.0.0.1"
DB_PORT = 5432
DB_NAME = "base_planer" 
DB_USER = "postgres"
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

STATUSES = ("Новая", "В работе", "Выполнена")
SORT_OPTIONS = {
    "newest": "created_at DESC, id DESC",
    "oldest": "created_at ASC, id ASC",
    "due_date": "due_date ASC NULLS LAST, id DESC",
    "title_asc": "title ASC, id DESC",
    "title_desc": "title DESC, id DESC",
}


def get_db_connection():
    try:
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            client_encoding="UTF8",
        )
    except psycopg2.Error as error:
        print(f"Ошибка подключения к PostgreSQL: {error}")
        raise


@contextmanager
def db_connection():
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Войдите в аккаунт, чтобы открыть свои задачи.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped_view


def get_user_task(task_id):
    with db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT id, user_id, title, description, due_date, status,
                      created_at, updated_at
               FROM tasks WHERE id = %s AND user_id = %s""",
            (task_id, session["user_id"]),
        )
        return cur.fetchone()


def render_task_form(task=None, status=200, form_data=None):
    return render_template(
        "task_form.html",
        task=task,
        form_title="Редактирование задачи" if task else "Новая задача",
        form_data=form_data or {},
    ), status


def parse_due_date(value):
    if not value:
        return None

    # Проверяем строгий формат: две цифры, точка, две цифры, точка, четыре цифры.
    if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
        raise ValueError("Неверный формат даты")

    # Проверяем существование даты, включая високосные годы.
    return datetime.strptime(value, "%d.%m.%Y").date()


def date_error():
    flash(
        "Ошибка даты. Введите дату строго в формате ДД.ММ.ГГГГ, "
        "например 01.01.2026. Вариант 01012026 не допускается.",
        "error",
    )


@app.route("/")
def index():
    return render_template("index.html")



@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        flash("Введите логин и пароль.", "error")
        return render_template("auth.html"), 400

    try:
        with db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, username, password_hash
                    FROM users
                    WHERE username = %s
                    """,
                    (username,),
                )
                user = cur.fetchone()

    except psycopg2.Error as error:
        print(f"Ошибка авторизации: {error}")
        flash("Ошибка подключения к базе данных.", "error")
        return render_template("auth.html"), 500

    if user is None or not check_password_hash(
        user["password_hash"], password
    ):
        flash("Неверный логин или пароль.", "error")
        return render_template("auth.html"), 401

    session.clear()
    session["user_id"] = user["id"]
    session["username"] = user["username"]

    flash("Вы успешно вошли в аккаунт.", "success")
    return redirect(url_for("index"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    # Проверяем заполнение полей
    if not username or not email or not password or not confirm_password:
        flash("Заполните все поля.", "error")
        return render_template("register.html"), 400

    # Проверяем логин
    if len(username) > 50:
        flash("Логин не должен превышать 50 символов.", "error")
        return render_template("register.html"), 400

    # Проверяем электронную почту
    if (
        len(email) > 120
        or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
    ):
        flash("Введите корректную электронную почту.", "error")
        return render_template("register.html"), 400

    # Проверяем длину пароля
    if len(password) < 8:
        flash("Пароль должен содержать минимум 8 символов.", "error")
        return render_template("register.html"), 400

    # Проверяем подтверждение пароля
    if password != confirm_password:
        flash("Пароли не совпадают.", "error")
        return render_template("register.html"), 400

    # Создаём аккаунт
    password_hash = generate_password_hash(password)

    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (username, email, password_hash)
                    VALUES (%s, %s, %s)
                    """,
                    (username, email, password_hash),
                )

    except psycopg2.errors.UniqueViolation:
        flash("Этот логин или адрес электронной почты уже занят.", "error")
        return render_template("register.html"), 409

    except psycopg2.Error as error:
        print(f"Ошибка регистрации: {error}")
        flash("Не удалось создать аккаунт. Проверь подключение к базе.", "error")
        return render_template("register.html"), 500

    flash("Аккаунт успешно создан. Теперь войдите в него.", "success")
    return redirect(url_for("login"))


@app.route("/home")
@login_required
def home():
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    sort = request.args.get("sort", "newest")

    query = """SELECT id, title, description, due_date, status, created_at
               FROM tasks WHERE user_id = %s"""
    params = [session["user_id"]]

    if status in STATUSES:
        query += " AND status = %s"
        params.append(status)

    if search:
        query += " AND (title ILIKE %s OR COALESCE(description, '') ILIKE %s)"
        pattern = f"%{search}%"
        params.extend((pattern, pattern))

    query += " ORDER BY " + SORT_OPTIONS.get(sort, SORT_OPTIONS["newest"])

    with db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params)
        tasks = cur.fetchall()

    return render_template("home.html", tasks=tasks)


@app.route("/tasks/new", methods=["GET", "POST"])
@login_required
def create_task():
    if request.method == "GET":
        return render_task_form()

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    due_date_text = request.form.get("due_date", "").strip()
    form_data = {
        "title": title,
        "description": description,
        "due_date": due_date_text,
    }

    if not title or len(title) > 150:
        flash("Название обязательно и не должно превышать 150 символов.", "error")
        return render_task_form(status=400, form_data=form_data)

    try:
        due_date = parse_due_date(due_date_text)
    except ValueError:
        date_error()
        return render_task_form(status=400, form_data=form_data)

    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tasks (user_id, title, description, due_date)
                   VALUES (%s, %s, %s, %s)""",
                (session["user_id"], title, description, due_date),
            )
    except psycopg2.Error as error:
        print(f"Ошибка создания задачи: {error}")
        flash("Не удалось создать задачу.", "error")
        return redirect(url_for("home"))

    flash("Задача создана.", "success")
    return redirect(url_for("home"))


@app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def edit_task(task_id):
    try:
        task = get_user_task(task_id)
    except psycopg2.Error as error:
        print(f"Ошибка получения задачи: {error}")
        flash("Не удалось загрузить задачу.", "error")
        return redirect(url_for("home"))

    if task is None:
        flash("Задача не найдена.", "error")
        return redirect(url_for("home"))

    if request.method == "GET":
        return render_task_form(task)

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    due_date_text = request.form.get("due_date", "").strip()
    status = request.form.get("status", task["status"])
    form_data = {
        "title": title,
        "description": description,
        "due_date": due_date_text,
    }

    if not title or len(title) > 150:
        flash("Название обязательно и не должно превышать 150 символов.", "error")
        return render_task_form(task, 400, form_data)

    if status not in STATUSES:
        status = task["status"]

    try:
        due_date = parse_due_date(due_date_text)
    except ValueError:
        date_error()
        return render_task_form(task, 400, form_data)

    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE tasks
                   SET title = %s, description = %s, due_date = %s,
                       status = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s AND user_id = %s""",
                (title, description, due_date, status, task_id, session["user_id"]),
            )
    except psycopg2.Error as error:
        print(f"Ошибка редактирования задачи: {error}")
        flash("Не удалось сохранить изменения.", "error")
        return redirect(url_for("home"))

    flash("Задача обновлена.", "success")
    return redirect(url_for("home"))


@app.route("/tasks/<int:task_id>/status", methods=["POST"])
@login_required
def change_status(task_id):
    new_status = request.form.get("status", "")

    if new_status not in STATUSES:
        flash("Выбран недопустимый статус.", "error")
        return redirect(url_for("home"))

    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE tasks SET status = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s AND user_id = %s""",
                (new_status, task_id, session["user_id"]),
            )
            changed = cur.rowcount
    except psycopg2.Error as error:
        print(f"Ошибка изменения статуса: {error}")
        flash("Не удалось изменить статус.", "error")
        return redirect(url_for("home"))

    flash(
        "Статус обновлён." if changed else "Задача не найдена.",
        "success" if changed else "error",
    )
    return redirect(url_for("home"))


@app.route("/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id):
    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tasks WHERE id = %s AND user_id = %s",
                (task_id, session["user_id"]),
            )
            deleted = cur.rowcount
    except psycopg2.Error as error:
        print(f"Ошибка удаления задачи: {error}")
        flash("Не удалось удалить задачу.", "error")
        return redirect(url_for("home"))

    flash(
        "Задача удалена." if deleted else "Задача не найдена.",
        "success" if deleted else "error",
    )
    return redirect(url_for("home"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
