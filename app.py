from datetime import date, datetime, timedelta
from decimal import Decimal
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///presupuesto.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'dev'

db = SQLAlchemy(app)

# --------- CONSTANTES ---------
CYCLE_START_DAY = 25  # ciclo del 25 al 24

# --------- MODELOS ---------
class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), nullable=False, unique=True)
    saldo_inicial = db.Column(db.Numeric(12, 2), default=0)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), nullable=False, unique=True)
    tipo = db.Column(db.String(10), nullable=False)  # 'ingreso' | 'gasto'

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, nullable=False)
    concepto = db.Column(db.String(120), nullable=False)
    importe = db.Column(db.Numeric(12, 2), nullable=False)  # positivo
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    nota = db.Column(db.Text)

    account = db.relationship('Account', backref=db.backref('transactions', lazy=True))
    category = db.relationship('Category', backref=db.backref('transactions', lazy=True))

    @property
    def signed_amount(self):
        if self.category and self.category.tipo == 'ingreso':
            return Decimal(self.importe or 0)
        return -Decimal(self.importe or 0)

class Goal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    monto_objetivo = db.Column(db.Numeric(12, 2), nullable=False)
    fecha_limite = db.Column(db.Date, nullable=True)
    nota = db.Column(db.Text)
    aportes = db.relationship('GoalContribution', backref='goal', cascade='all, delete-orphan')

    @property
    def total_aportado(self):
        return sum((a.monto or Decimal('0')) for a in self.aportes) or Decimal('0')

    @property
    def porcentaje(self):
        if not self.monto_objetivo or Decimal(self.monto_objetivo) == 0:
            return 0.0
        return float((self.total_aportado / Decimal(self.monto_objetivo)) * 100)

class GoalContribution(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    goal_id = db.Column(db.Integer, db.ForeignKey('goal.id'), nullable=False)
    fecha = db.Column(db.Date, nullable=False)
    monto = db.Column(db.Numeric(12, 2), nullable=False)
    comentario = db.Column(db.String(200))

class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cycle_start = db.Column(db.Date, nullable=False, unique=True)
    income_estimated = db.Column(db.Numeric(12, 2), default=0)
    note = db.Column(db.Text)
    lines = db.relationship('BudgetLine', backref='budget', cascade='all, delete-orphan')

class BudgetLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    budget_id = db.Column(db.Integer, db.ForeignKey('budget.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    category = db.relationship('Category')

# --------- INIT / SEED ---------
@app.before_request
def _startup():
    db.create_all()
    if Account.query.count() == 0:
        db.session.add_all([
            Account(nombre='Banco', saldo_inicial=Decimal('0.00')),
            Account(nombre='Efectivo', saldo_inicial=Decimal('0.00')),
        ])
    if Category.query.count() == 0:
        db.session.add_all([
            Category(nombre='Salario', tipo='ingreso'),
            Category(nombre='Supermercado', tipo='gasto'),
            Category(nombre='Transporte', tipo='gasto'),
            Category(nombre='Otros ingresos', tipo='ingreso'),
            Category(nombre='Otros gastos', tipo='gasto'),
        ])
    db.session.commit()

# --------- HELPERS ---------
def parse_date(s, default=None):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return default

def cycle_bounds(ref: date, start_day: int = CYCLE_START_DAY):
    start = date(ref.year, ref.month, start_day)
    if ref < start:
        start = date(start.year - 1, 12, start_day) if start.month == 1 else date(start.year, start.month - 1, start_day)
    end = date(start.year + 1, 1, start_day) if start.month == 12 else date(start.year, start.month + 1, start_day)
    return start, end

# --------- RUTAS ---------
@app.route('/')
def home():
    d1, _ = cycle_bounds(date.today(), CYCLE_START_DAY)
    return redirect(url_for('resumen', y=d1.year, m=d1.month))

@app.route('/resumen')
def resumen():
    y_arg, m_arg = request.args.get('y'), request.args.get('m')
    ref = date(int(y_arg), int(m_arg), CYCLE_START_DAY) if y_arg and m_arg else date.today()
    d1, d2 = cycle_bounds(ref, CYCLE_START_DAY)
    fin_inclusivo = d2 - timedelta(days=1)

    tx = Transaction.query.filter(Transaction.fecha >= d1, Transaction.fecha < d2).all()
    ingresos = sum(float(t.importe) for t in tx if t.category and t.category.tipo == 'ingreso')
    gastos   = sum(float(t.importe) for t in tx if t.category and t.category.tipo == 'gasto')
    balance  = ingresos - gastos

    cuentas = Account.query.order_by(Account.nombre).all()
    saldos = []
    for c in cuentas:
        movs = Transaction.query.filter_by(account_id=c.id).all()
        total_movs = sum(float(t.signed_amount) for t in movs)
        saldos.append({'cuenta': c, 'saldo': float(c.saldo_inicial) + total_movs})

    por_cat = {}
    for t in tx:
        key = (t.category.nombre, t.category.tipo) if t.category else ('Sin categoría', 'gasto')
        por_cat[key] = por_cat.get(key, 0.0) + float(t.importe)

    prev_anchor, next_anchor = d1 - timedelta(days=1), d2
    prev_y, prev_m = prev_anchor.year, prev_anchor.month
    next_y, next_m = next_anchor.year, next_anchor.month

    return render_template('resumen.html',
        d1=d1, fin_inclusivo=fin_inclusivo,
        ingresos=ingresos, gastos=gastos, balance=balance,
        saldos=saldos, por_cat=por_cat,
        prev_y=prev_y, prev_m=prev_m, next_y=next_y, next_m=next_m
    )

# ---- Transacciones ----
@app.route('/transacciones')
def transactions_index():
    q = Transaction.query
    fd = parse_date(request.args.get('desde',''), None)
    fh = parse_date(request.args.get('hasta',''), None)

    default_cycle = False
    if not fd and not fh:
        d1, d2 = cycle_bounds(date.today(), CYCLE_START_DAY)
        fd, fh = d1, d2 - timedelta(days=1)
        default_cycle = True

    if fd: q = q.filter(Transaction.fecha >= fd)
    if fh: q = q.filter(Transaction.fecha <= fh)

    account_id = request.args.get('account_id') or ''
    category_id = request.args.get('category_id') or ''
    if account_id: q = q.filter_by(account_id=account_id)
    if category_id: q = q.filter_by(category_id=category_id)

    texto = (request.args.get('q') or '').strip()
    if texto: q = q.filter(Transaction.concepto.ilike(f"%{texto}%"))

    items = q.order_by(Transaction.fecha.desc(), Transaction.id.desc()).all()
    accounts = Account.query.order_by(Account.nombre).all()
    categories = Category.query.order_by(Category.tipo.desc(), Category.nombre).all()

    return render_template('transactions_index.html',
        items=items, accounts=accounts, categories=categories,
        desde_value=fd.strftime('%Y-%m-%d') if fd else '',
        hasta_value=fh.strftime('%Y-%m-%d') if fh else '',
        account_selected=str(account_id),
        category_selected=str(category_id),
        texto_busqueda=texto,
        default_cycle=default_cycle
    )

@app.route('/transacciones/nueva', methods=['GET','POST'])
def transactions_new():
    accounts = Account.query.order_by(Account.nombre).all()
    categories = Category.query.order_by(Category.tipo.desc(), Category.nombre).all()
    if request.method == 'POST':
        fecha = parse_date(request.form.get('fecha'), date.today())
        concepto = request.form.get('concepto','').strip()
        importe_s = request.form.get('importe','0').replace(',', '.')
        account_id = int(request.form.get('account_id'))
        category_id = int(request.form.get('category_id'))
        nota = request.form.get('nota','').strip()

        if not concepto:
            flash('El concepto es obligatorio', 'error')
            return render_template('transactions_form.html', accounts=accounts, categories=categories)
        try:
            imp = Decimal(importe_s)
            if imp <= 0: raise ValueError()
        except Exception:
            flash('Importe inválido', 'error')
            return render_template('transactions_form.html', accounts=accounts, categories=categories)

        t = Transaction(fecha=fecha, concepto=concepto, importe=imp,
                        account_id=account_id, category_id=category_id, nota=nota)
        db.session.add(t); db.session.commit()
        flash('Transacción creada', 'ok')
        return redirect(url_for('transactions_index'))

    return render_template('transactions_form.html', accounts=accounts, categories=categories)

@app.route('/transacciones/<int:tx_id>/editar', methods=['GET','POST'])
def transactions_edit(tx_id):
    t = Transaction.query.get_or_404(tx_id)
    accounts = Account.query.order_by(Account.nombre).all()
    categories = Category.query.order_by(Category.tipo.desc(), Category.nombre).all()

    if request.method == 'POST':
        t.fecha = parse_date(request.form.get('fecha'), t.fecha)
        t.concepto = request.form.get('concepto','').strip()
        t.importe = Decimal(request.form.get('importe','0').replace(',', '.'))
        t.account_id = int(request.form.get('account_id'))
        t.category_id = int(request.form.get('category_id'))
        t.nota = request.form.get('nota','').strip()
        if not t.concepto or t.importe <= 0:
            flash('Revisa concepto e importe', 'error')
            return render_template('transactions_form.html', accounts=accounts, categories=categories, item=t, modo='edit')
        db.session.commit()
        flash('Transacción actualizada', 'ok')
        return redirect(url_for('transactions_index'))

    return render_template('transactions_form.html', accounts=accounts, categories=categories, item=t, modo='edit'))

@app.route('/transacciones/<int:tx_id>/eliminar', methods=['POST'])
def transactions_delete(tx_id):
    t = Transaction.query.get_or_404(tx_id)
    db.session.delete(t); db.session.commit()
    flash('Transacción eliminada', 'ok')
    return redirect(url_for('transactions_index'))

# ---- Categorías ----
@app.route('/categorias', methods=['GET','POST'])
def categories_index():
    if request.method == 'POST':
        nombre = request.form.get('nombre','').strip()
        tipo = request.form.get('tipo','gasto')
        if not nombre:
            flash('El nombre es obligatorio', 'error')
        elif tipo not in ('ingreso','gasto'):
            flash('Tipo inválido', 'error')
        else:
            try:
                db.session.add(Category(nombre=nombre, tipo=tipo))
                db.session.commit()
                flash('Categoría creada', 'ok')
            except Exception:
                db.session.rollback()
                flash('No se pudo crear (¿nombre duplicado?)', 'error')
        return redirect(url_for('categories_index'))

    cats = Category.query.order_by(Category.tipo.desc(), Category.nombre).all()
    return render_template('categories_index.html', categories=cats)

@app.route('/categorias/<int:cat_id>/eliminar', methods=['POST'])
def category_delete(cat_id):
    c = Category.query.get_or_404(cat_id)
    if c.transactions:
        flash('No se puede eliminar: tiene transacciones', 'error')
        return redirect(url_for('categories_index'))
    db.session.delete(c); db.session.commit()
    flash('Categoría eliminada', 'ok')
    return redirect(url_for('categories_index'))

# ---- Cuentas ----
@app.route('/cuentas', methods=['GET','POST'])
def accounts_index():
    if request.method == 'POST':
        nombre = request.form.get('nombre','').strip()
        saldo_s = request.form.get('saldo_inicial','0').replace(',', '.')
        try:
            s = Decimal(saldo_s)
        except Exception:
            s = Decimal('0.00')
        if not nombre:
            flash('El nombre es obligatorio', 'error')
        else:
            try:
                db.session.add(Account(nombre=nombre, saldo_inicial=s))
                db.session.commit()
                flash('Cuenta creada', 'ok')
            except Exception:
                db.session.rollback()
                flash('No se pudo crear (¿nombre duplicado?)', 'error')
        return redirect(url_for('accounts_index'))

    cuentas = Account.query.order_by(Account.nombre).all()
    return render_template('accounts_index.html', accounts=cuentas)

@app.route('/cuentas/<int:acc_id>/eliminar', methods=['POST'])
def accounts_delete(acc_id):
    a = Account.query.get_or_404(acc_id)
    if a.transactions:
        flash('No se puede eliminar: tiene transacciones', 'error')
        return redirect(url_for('accounts_index'))
    db.session.delete(a); db.session.commit()
    flash('Cuenta eliminada', 'ok')
    return redirect(url_for('accounts_index'))

# ---- OBJETIVOS ----
@app.route('/objetivos', methods=['GET','POST'])
def goals_index():
    if request.method == 'POST':
        nombre = (request.form.get('nombre') or '').strip()
        objetivo = (request.form.get('monto_objetivo') or '0').replace(',', '.')
        fecha_limite = parse_date(request.form.get('fecha_limite') or '', None)
        nota = (request.form.get('nota') or '').strip()
        try:
            obj = Decimal(objetivo)
            if not nombre or obj <= 0:
                raise ValueError()
        except Exception:
            flash('Revisa el nombre y el importe objetivo (>0).', 'error')
            return redirect(url_for('goals_index'))
        g = Goal(nombre=nombre, monto_objetivo=obj, fecha_limite=fecha_limite, nota=nota)
        db.session.add(g); db.session.commit()
        flash('Objetivo creado', 'ok')
        return redirect(url_for('goals_index'))

    goals = Goal.query.order_by(Goal.fecha_limite.is_(None), Goal.fecha_limite).all()
    return render_template('goals_index.html', goals=goals)

@app.route('/objetivos/<int:goal_id>/eliminar', methods=['POST'])
def goals_delete(goal_id):
    g = Goal.query.get_or_404(goal_id)
    db.session.delete(g); db.session.commit()
    flash('Objetivo eliminado', 'ok')
    return redirect(url_for('goals_index'))

@app.route('/objetivos/<int:goal_id>/aporte', methods=['POST'])
def goals_add_contribution(goal_id):
    g = Goal.query.get_or_404(goal_id)
    fecha = parse_date(request.form.get('fecha') or '', date.today())
    monto_s = (request.form.get('monto') or '0').replace(',', '.')
    comentario = (request.form.get('comentario') or '').strip()
    try:
        monto = Decimal(monto_s)
        if monto <= 0:
            raise ValueError()
    except Exception:
        flash('Aporte inválido.', 'error')
        return redirect(url_for('goals_index'))
    a = GoalContribution(goal_id=g.id, fecha=fecha, monto=monto, comentario=comentario)
    db.session.add(a); db.session.commit()
    flash('Aporte registrado', 'ok')
    return redirect(url_for('goals_index'))

@app.route('/objetivos/<int:goal_id>/aporte/<int:aid>/eliminar', methods=['POST'])
def goals_delete_contribution(goal_id, aid):
    a = GoalContribution.query.get_or_404(aid)
    db.session.delete(a); db.session.commit()
    flash('Aporte eliminado', 'ok')
    return redirect(url_for('goals_index'))

# ---- PRESUPUESTO ----
@app.route('/presupuesto', methods=['GET', 'POST'])
def budget_index():
    # y/m = mes del inicio del ciclo (25)
    y_arg, m_arg = request.args.get('y'), request.args.get('m')
    if request.method == 'POST':
        y_arg = request.form.get('y')
        m_arg = request.form.get('m')

    ref = date(int(y_arg), int(m_arg), CYCLE_START_DAY) if y_arg and m_arg else date.today()
    d1, d2 = cycle_bounds(ref, CYCLE_START_DAY)

    budget = Budget.query.filter_by(cycle_start=d1).first()
    if request.method == 'POST':
        if not budget:
            budget = Budget(cycle_start=d1)
            db.session.add(budget)

        # Guardar ingreso estimado (Decimal en BD)
        income_estimated_s = (request.form.get('income_estimated') or '0').replace(',', '.')
        try:
            budget.income_estimated = Decimal(income_estimated_s)
        except Exception:
            budget.income_estimated = Decimal('0.00')

        # Categorías de gasto (todo lo que no sea 'ingreso')
        cats_all = Category.query.order_by(Category.nombre).all()
        gasto_cats = [c for c in cats_all if (c.tipo or '').strip().lower() != 'ingreso']

        current_lines = {bl.category_id: bl for bl in budget.lines}
        for c in gasto_cats:
            val_s = (request.form.get(f'cat_{c.id}') or '').strip()
            if val_s == '':
                continue
            try:
                amt = Decimal(val_s.replace(',', '.'))
            except Exception:
                amt = Decimal('0')
            if amt > 0:
                if c.id in current_lines:
                    current_lines[c.id].amount = amt
                else:
                    db.session.add(BudgetLine(budget=budget, category_id=c.id, amount=amt))
            else:
                if c.id in current_lines:
                    db.session.delete(current_lines[c.id])

        db.session.commit()
        flash('Presupuesto guardado', 'ok')
        return redirect(url_for('budget_index', y=d1.year, m=d1.month))

    # ---------- GET: preparar datos (todo en float para la vista) ----------
    cats_all = Category.query.order_by(Category.nombre).all()
    gasto_cats = [c for c in cats_all if (c.tipo or '').strip().lower() != 'ingreso']

    # Ingreso estimado y líneas -> floats
    line_by_cat = {}
    income_estimated = 0.0
    if budget:
        income_estimated = float(budget.income_estimated or 0)
        for bl in budget.lines:
            line_by_cat[bl.category_id] = float(bl.amount or 0)

    # Gastado real por categoría en el ciclo -> floats
    tx_cycle = Transaction.query.filter(Transaction.fecha >= d1, Transaction.fecha < d2).all()
    spent_by_cat = {}
    for t in tx_cycle:
        if t.category and (t.category.tipo or '').strip().lower() != 'ingreso':
            spent_by_cat[t.category_id] = spent_by_cat.get(t.category_id, 0.0) + float(t.importe)

    # Totales -> floats
    total_budget = sum(line_by_cat.get(c.id, 0.0) for c in gasto_cats)
    total_spent  = sum(spent_by_cat.get(c.id, 0.0) for c in gasto_cats)
    total_income_real = sum(float(t.importe) for t in tx_cycle
                            if t.category and (t.category.tipo or '').strip().lower() == 'ingreso')

    prev_anchor = d1 - timedelta(days=1)
    next_anchor = d2
    prev_y, prev_m = prev_anchor.year, prev_anchor.month
    next_y, next_m = next_anchor.year, next_anchor.month

    # Para evitar cualquier Decimal en la vista, convertir estructuras a float seguros
    line_by_cat = {int(k): float(v) for k, v in line_by_cat.items()}
    spent_by_cat = {int(k): float(v) for k, v in spent_by_cat.items()}

    return render_template('budget_index.html',
        d1=d1, d2=d2, fin_inclusivo=d2 - timedelta(days=1),
        gasto_cats=gasto_cats,
        line_by_cat=line_by_cat,
        spent_by_cat=spent_by_cat,
        income_estimated=float(income_estimated),
        total_budget=float(total_budget),
        total_spent=float(total_spent),
        total_income_real=float(total_income_real),
        y=d1.year, m=d1.month, prev_y=prev_y, prev_m=prev_m, next_y=next_y, next_m=next_m
    )

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
