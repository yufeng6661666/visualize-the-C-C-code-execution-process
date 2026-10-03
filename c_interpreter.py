"""
A small C language interpreter that supports step-by-step execution.

Features:
  - Tokenizer + recursive-descent parser -> AST
  - Executor that records a history snapshot after every executed statement
  - Scope tracking (global + per-function locals)
  - Supports: int/float/double/char, arrays, pointers (basic),
    if/else, for/while/do-while, break/continue/return,
    functions, printf, scanf, ++/--, compound assignments, etc.
  - Forward/backward stepping is implemented by navigating the recorded
    history list (snapshots).
"""

import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple


# ---------------------------------------------------------------------------
# AST node types
# ---------------------------------------------------------------------------

@dataclass
class Node:
    line: int = 0


@dataclass
class Program(Node):
    decls: List[Node] = field(default_factory=list)


@dataclass
class VarDecl(Node):
    vtype: str = "int"
    name: str = ""
    init: Optional[Node] = None
    array_size: Optional[Node] = None  # for arrays


@dataclass
class FuncDef(Node):
    rtype: str = "int"
    name: str = ""
    params: List[Tuple[str, str]] = field(default_factory=list)  # (type, name)
    body: Optional[Node] = None  # Block


@dataclass
class Block(Node):
    stmts: List[Node] = field(default_factory=list)


@dataclass
class IfStmt(Node):
    cond: Optional[Node] = None
    then_branch: Optional[Node] = None
    else_branch: Optional[Node] = None


@dataclass
class ForStmt(Node):
    init: Optional[Node] = None
    cond: Optional[Node] = None
    update: Optional[Node] = None
    body: Optional[Node] = None


@dataclass
class WhileStmt(Node):
    cond: Optional[Node] = None
    body: Optional[Node] = None


@dataclass
class DoWhileStmt(Node):
    body: Optional[Node] = None
    cond: Optional[Node] = None


@dataclass
class ReturnStmt(Node):
    expr: Optional[Node] = None


@dataclass
class BreakStmt(Node):
    pass


@dataclass
class ContinueStmt(Node):
    pass


@dataclass
class ExprStmt(Node):
    expr: Optional[Node] = None


@dataclass
class BinaryOp(Node):
    op: str = ""
    left: Optional[Node] = None
    right: Optional[Node] = None


@dataclass
class UnaryOp(Node):
    op: str = ""
    operand: Optional[Node] = None
    prefix: bool = True


@dataclass
class AssignOp(Node):
    op: str = "="
    target: Optional[Node] = None
    value: Optional[Node] = None


@dataclass
class Literal(Node):
    value: Any = None
    ltype: str = "int"  # int, float, char, string


@dataclass
class VarRef(Node):
    name: str = ""


@dataclass
class ArrayAccess(Node):
    array: Optional[Node] = None
    index: Optional[Node] = None


@dataclass
class FuncCall(Node):
    name: str = ""
    args: List[Node] = field(default_factory=list)
    target: Optional[Node] = None  # for member calls like a.begin()


@dataclass
class TernaryOp(Node):
    cond: Optional[Node] = None
    true_expr: Optional[Node] = None
    false_expr: Optional[Node] = None


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

KEYWORDS = {
    "int", "float", "double", "char", "void", "short", "long", "unsigned",
    "signed", "if", "else", "for", "while", "do", "return", "break",
    "continue", "const", "static", "extern", "struct", "typedef", "sizeof",
    "switch", "case", "default", "goto", "enum", "union", "volatile",
}

TOKEN_SPEC = [
    ("MLCOMMENT", r"/\*.*?\*/"),
    ("LINECOMMENT", r"//[^\n]*"),
    ("PREPROC", r"#[^\n]*"),
    ("STRING", r'"(?:\\.|[^"\\])*"'),
    ("CHAR", r"'(?:\\.|[^'\\])'"),
    ("NUMBER", r"0[xX][0-9a-fA-F]+|\d+\.\d+([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?|\d+[eE][+-]?\d+|\d+"),
    ("OP", r">>=|<<=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|==|!=|<=|>=|&&|\|\||<<|>>|\+\+|--|->|\+|-|\*|/|%|=|<|>|!|&|\||\^|~|\?|:"),
    ("IDENT", r"[A-Za-z_]\w*"),
    ("PUNCT", r"[{}()\[\];,\.]"),
    ("NEWLINE", r"\n"),
    ("SKIP", r"[ \t\r]+"),
]

MASTER_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in TOKEN_SPEC), re.DOTALL)


@dataclass
class Token:
    type: str
    value: str
    line: int


def tokenize(code: str) -> List[Token]:
    # Normalize line endings so line counting is consistent
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    tokens: List[Token] = []
    line = 1
    for m in MASTER_RE.finditer(code):
        kind = m.lastgroup
        value = m.group()
        nl_count = value.count("\n")
        start_line = line
        if kind in ("SKIP", "NEWLINE"):
            line += nl_count
            continue
        if kind in ("LINECOMMENT", "MLCOMMENT", "PREPROC"):
            line += nl_count
            continue
        tokens.append(Token(kind, value, start_line))
        line += nl_count
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self, offset: int = 0) -> Optional[Token]:
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return None

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def expect(self, value: str) -> Token:
        t = self.peek()
        if t is None or t.value != value:
            raise SyntaxError(f"Expected '{value}' but got {t.value if t else 'EOF'} at line {t.line if t else '?'}")
        return self.advance()

    def match(self, value: str) -> bool:
        t = self.peek()
        if t is not None and t.value == value:
            self.advance()
            return True
        return False

    def at_type_keyword(self) -> bool:
        t = self.peek()
        return t is not None and t.type == "IDENT" and t.value in (
            "int", "float", "double", "char", "void", "short", "long",
            "unsigned", "signed", "const", "static", "extern", "struct",
        )

    def parse_type(self) -> str:
        # consume type specifiers; keep last base type
        base = "int"
        while self.at_type_keyword():
            t = self.advance()
            if t.value in ("int", "float", "double", "char", "void", "short", "long"):
                base = t.value
            # ignore const/static/extern/unsigned/signed/struct for now
        # handle pointers
        ptrs = 0
        while self.peek() is not None and self.peek().value == "*":
            self.advance()
            ptrs += 1
        return base + "*" * ptrs

    # ---- top level ----
    def parse_program(self) -> Program:
        decls = []
        while self.peek() is not None:
            if self.peek().value == "using":
                self._skip_until_semicolon()
                continue
            if self.peek().value == "namespace":
                self._skip_namespace()
                continue
            decls.append(self.parse_declaration())
        return Program(decls=decls, line=1)

    def _skip_until_semicolon(self):
        depth = 0
        while self.peek() is not None:
            t = self.advance()
            if t.value in ("(", "{", "["):
                depth += 1
            elif t.value in (")", "}", "]"):
                depth -= 1
            elif t.value == ";" and depth == 0:
                return

    def _skip_namespace(self):
        # namespace name { ... }  -> skip the block
        self.advance()  # namespace
        # optional name
        if self.peek() is not None and self.peek().type == "IDENT":
            self.advance()
        if self.match("{"):
            depth = 1
            while self.peek() is not None and depth > 0:
                t = self.advance()
                if t.value == "{":
                    depth += 1
                elif t.value == "}":
                    depth -= 1
        else:
            self._skip_until_semicolon()

    def parse_declaration(self) -> Node:
        line = self.peek().line if self.peek() else 0
        vtype = self.parse_type()
        # Now it could be a function definition or a global variable declaration
        name_tok = self.peek()
        if name_tok is None:
            raise SyntaxError("Expected identifier after type")
        if name_tok.type != "IDENT":
            raise SyntaxError(f"Expected identifier, got {name_tok.value}")
        name = name_tok.value
        self.advance()

        if self.peek() is not None and self.peek().value == "(":
            # function definition
            self.advance()  # (
            params = []
            if not (self.peek() is not None and self.peek().value == ")"):
                while True:
                    ptype = self.parse_type()
                    pname_tok = self.peek()
                    pname = pname_tok.value if pname_tok and pname_tok.type == "IDENT" else ""
                    if pname:
                        self.advance()
                    params.append((ptype, pname))
                    if not self.match(","):
                        break
            self.expect(")")
            # body
            if self.peek() is not None and self.peek().value == "{":
                body = self.parse_block()
            else:
                # forward declaration without body
                self.match(";")
                body = None
            return FuncDef(rtype=vtype, name=name, params=params, body=body, line=line)
        else:
            # variable declaration (possibly multiple, comma separated)
            decls = self.parse_var_decl_rest(vtype, name, line)
            # parse_var_decl_rest handles the trailing ; and multiple vars
            return decls[0] if len(decls) == 1 else _MultiDecl(decls=decls, line=line)

    def parse_var_decl_rest(self, vtype: str, first_name: str, line: int) -> List[VarDecl]:
        result = []
        name = first_name
        while True:
            array_size = None
            if self.peek() is not None and self.peek().value == "[":
                self.advance()
                array_size = self.parse_expr()
                self.expect("]")
            init = None
            if self.match("="):
                init = self.parse_expr()
            result.append(VarDecl(vtype=vtype, name=name, init=init, array_size=array_size, line=line))
            if self.match(","):
                nt = self.peek()
                if nt is None or nt.type != "IDENT":
                    raise SyntaxError("Expected variable name after ','")
                name = nt.value
                self.advance()
                continue
            break
        self.expect(";")
        return result

    # ---- statements ----
    def parse_block(self) -> Block:
        line = self.peek().line if self.peek() else 0
        self.expect("{")
        stmts = []
        while self.peek() is not None and self.peek().value != "}":
            stmts.append(self.parse_statement())
        self.expect("}")
        return Block(stmts=stmts, line=line)

    def parse_statement(self) -> Node:
        t = self.peek()
        if t is None:
            raise SyntaxError("Unexpected end of input")
        if t.value == "using":
            self._skip_until_semicolon()
            return ExprStmt(expr=None, line=t.line)
        if t.value == "{":
            return self.parse_block()
        if t.value == "if":
            return self.parse_if()
        if t.value == "for":
            return self.parse_for()
        if t.value == "while":
            return self.parse_while()
        if t.value == "do":
            return self.parse_do_while()
        if t.value == "return":
            return self.parse_return()
        if t.value == "break":
            self.advance()
            self.expect(";")
            return BreakStmt(line=t.line)
        if t.value == "continue":
            self.advance()
            self.expect(";")
            return ContinueStmt(line=t.line)
        if t.value == ";":
            self.advance()
            return ExprStmt(expr=None, line=t.line)
        if self.at_type_keyword():
            vtype = self.parse_type()
            nt = self.peek()
            if nt is None or nt.type != "IDENT":
                raise SyntaxError(f"Expected identifier after type, line {t.line}")
            name = nt.value
            self.advance()
            decls = self.parse_var_decl_rest(vtype, name, t.line)
            return decls[0] if len(decls) == 1 else _MultiDecl(decls=decls, line=t.line)
        # expression statement
        expr = self.parse_expr()
        self.expect(";")
        return ExprStmt(expr=expr, line=t.line)

    def parse_if(self) -> IfStmt:
        line = self.peek().line
        self.advance()  # if
        self.expect("(")
        cond = self.parse_expr()
        self.expect(")")
        then_branch = self.parse_statement()
        else_branch = None
        if self.match("else"):
            else_branch = self.parse_statement()
        return IfStmt(cond=cond, then_branch=then_branch, else_branch=else_branch, line=line)

    def parse_for(self) -> ForStmt:
        line = self.peek().line
        self.advance()
        self.expect("(")
        init = None
        if not self.match(";"):
            if self.at_type_keyword():
                vtype = self.parse_type()
                nt = self.peek()
                name = nt.value
                self.advance()
                decls = self.parse_var_decl_rest(vtype, name, line)
                init = decls[0] if len(decls) == 1 else _MultiDecl(decls=decls, line=line)
            else:
                e = self.parse_expr()
                self.expect(";")
                init = ExprStmt(expr=e, line=line)
        cond = None
        if not self.match(";"):
            cond = self.parse_expr()
            self.expect(";")
        update = None
        if not self.match(")"):
            update = self.parse_expr()
            self.expect(")")
        body = self.parse_statement()
        return ForStmt(init=init, cond=cond, update=update, body=body, line=line)

    def parse_while(self) -> WhileStmt:
        line = self.peek().line
        self.advance()
        self.expect("(")
        cond = self.parse_expr()
        self.expect(")")
        body = self.parse_statement()
        return WhileStmt(cond=cond, body=body, line=line)

    def parse_do_while(self) -> DoWhileStmt:
        line = self.peek().line
        self.advance()
        body = self.parse_statement()
        self.expect("while")
        self.expect("(")
        cond = self.parse_expr()
        self.expect(")")
        self.expect(";")
        return DoWhileStmt(body=body, cond=cond, line=line)

    def parse_return(self) -> ReturnStmt:
        line = self.peek().line
        self.advance()
        expr = None
        if self.peek() is not None and self.peek().value != ";":
            expr = self.parse_expr()
        self.expect(";")
        return ReturnStmt(expr=expr, line=line)

    # ---- expressions (precedence climbing) ----
    def parse_expr(self) -> Node:
        return self.parse_assignment()

    def parse_assignment(self) -> Node:
        left = self.parse_conditional()
        t = self.peek()
        if t is not None and t.value in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="):
            op = self.advance().value
            right = self.parse_assignment()
            return AssignOp(op=op, target=left, value=right, line=left.line)
        return left

    def parse_conditional(self) -> Node:
        cond = self.parse_logical_or()
        if self.match("?"):
            true_e = self.parse_expr()
            self.expect(":")
            false_e = self.parse_conditional()
            return TernaryOp(cond=cond, true_expr=true_e, false_expr=false_e, line=cond.line)
        return cond

    def _parse_binary(self, lower_parser, ops) -> Node:
        left = lower_parser()
        while True:
            t = self.peek()
            if t is not None and t.value in ops:
                op = self.advance().value
                right = lower_parser()
                left = BinaryOp(op=op, left=left, right=right, line=t.line)
            else:
                break
        return left

    def parse_logical_or(self):
        return self._parse_binary(self.parse_logical_and, ("||",))

    def parse_logical_and(self):
        return self._parse_binary(self.parse_bitwise_or, ("&&",))

    def parse_bitwise_or(self):
        return self._parse_binary(self.parse_bitwise_xor, ("|",))

    def parse_bitwise_xor(self):
        return self._parse_binary(self.parse_bitwise_and, ("^",))

    def parse_bitwise_and(self):
        return self._parse_binary(self.parse_equality, ("&",))

    def parse_equality(self):
        return self._parse_binary(self.parse_relational, ("==", "!="))

    def parse_relational(self):
        return self._parse_binary(self.parse_shift, ("<", ">", "<=", ">="))

    def parse_shift(self):
        return self._parse_binary(self.parse_additive, ("<<", ">>"))

    def parse_additive(self):
        return self._parse_binary(self.parse_multiplicative, ("+", "-"))

    def parse_multiplicative(self):
        return self._parse_binary(self.parse_unary, ("*", "/", "%"))

    def parse_unary(self) -> Node:
        t = self.peek()
        if t is not None and t.value in ("++", "--", "+", "-", "!", "~", "*", "&"):
            op = self.advance().value
            operand = self.parse_unary()
            return UnaryOp(op=op, operand=operand, prefix=True, line=t.line)
        return self.parse_postfix()

    def parse_postfix(self) -> Node:
        expr = self.parse_primary()
        while True:
            t = self.peek()
            if t is None:
                break
            if t.value == "(":
                self.advance()
                args = []
                if not (self.peek() is not None and self.peek().value == ")"):
                    while True:
                        args.append(self.parse_expr())
                        if not self.match(","):
                            break
                self.expect(")")
                expr = FuncCall(name=expr.name if isinstance(expr, VarRef) else "",
                                args=args, line=t.line)
                # if expr wasn't a simple var ref, this is wrong but for our subset it's fine
            elif t.value == "[":
                self.advance()
                idx = self.parse_expr()
                self.expect("]")
                expr = ArrayAccess(array=expr, index=idx, line=t.line)
            elif t.value in ("++", "--"):
                op = self.advance().value
                expr = UnaryOp(op=op, operand=expr, prefix=False, line=t.line)
            elif t.value == "." or t.value == "->":
                self.advance()
                nt = self.peek()
                if nt and nt.type == "IDENT":
                    member = self.advance().value
                    # check if it's a member function call: obj.method(args)
                    if self.peek() is not None and self.peek().value == "(":
                        self.advance()
                        args = []
                        if not (self.peek() is not None and self.peek().value == ")"):
                            while True:
                                args.append(self.parse_expr())
                                if not self.match(","):
                                    break
                        self.expect(")")
                        expr = FuncCall(name=member, args=args, target=expr, line=t.line)
                    else:
                        # member data access (ignored for now)
                        expr = VarRef(name=member, line=t.line)
            else:
                break
        return expr

    def parse_primary(self) -> Node:
        t = self.peek()
        if t is None:
            raise SyntaxError("Unexpected end of expression")
        if t.type == "NUMBER":
            self.advance()
            val, ltype = self._parse_number(t.value)
            return Literal(value=val, ltype=ltype, line=t.line)
        if t.type == "STRING":
            self.advance()
            s = t.value[1:-1]
            s = self._unescape(s)
            return Literal(value=s, ltype="string", line=t.line)
        if t.type == "CHAR":
            self.advance()
            c = t.value[1:-1]
            c = self._unescape(c)
            return Literal(value=ord(c[0]) if c else 0, ltype="char", line=t.line)
        if t.type == "IDENT":
            self.advance()
            return VarRef(name=t.value, line=t.line)
        if t.value == "(":
            self.advance()
            e = self.parse_expr()
            self.expect(")")
            return e
        if t.value == "{":
            # initializer list: {expr, expr, ...}
            self.advance()
            items = []
            if self.peek() is not None and self.peek().value != "}":
                while True:
                    items.append(self.parse_expr())
                    if not self.match(","):
                        break
            self.expect("}")
            # store as a Literal with a list value
            return Literal(value=items, ltype="initlist", line=t.line)
        raise SyntaxError(f"Unexpected token {t.value} at line {t.line}")

    @staticmethod
    def _parse_number(s: str):
        if s.startswith(("0x", "0X")):
            return int(s, 16), "int"
        if "." in s or "e" in s or "E" in s:
            return float(s), "float"
        return int(s), "int"

    @staticmethod
    def _unescape(s: str) -> str:
        out = []
        i = 0
        while i < len(s):
            c = s[i]
            if c == "\\" and i + 1 < len(s):
                nxt = s[i + 1]
                mapping = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\",
                           '"': '"', "'": "'", "0": "\0"}
                out.append(mapping.get(nxt, nxt))
                i += 2
            else:
                out.append(c)
                i += 1
        return "".join(out)


@dataclass
class _MultiDecl(Node):
    decls: List[VarDecl] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class BreakException(Exception):
    pass


class ContinueException(Exception):
    pass


class ReturnException(Exception):
    def __init__(self, value):
        super().__init__()
        self.value = value


class Frame:
    def __init__(self, func_name: str):
        self.func_name = func_name
        self.locals: Dict[str, Tuple[str, Any]] = {}  # name -> (type, value)


def type_name(vtype: str) -> str:
    return vtype


def default_value(vtype: str):
    base = vtype.rstrip("*")
    if vtype.endswith("*"):
        return 0  # NULL pointer
    if base == "int" or base == "short" or base == "long" or base == "char":
        return 0
    if base in ("float", "double"):
        return 0.0
    return 0


class _CoutSentinel:
    """Stand-in for C++ std::cout."""
    def __repr__(self):
        return "cout"


class _CinSentinel:
    """Stand-in for C++ std::cin."""
    def __repr__(self):
        return "cin"


class _EndlSentinel:
    """Stand-in for C++ std::endl."""
    def __repr__(self):
        return "endl"


class _Iterator:
    """Stand-in for C++ iterator pointing into an array (list)."""
    def __init__(self, array, index: int):
        self.array = array  # the actual list object
        self.index = index

    def __repr__(self):
        return f"iter[{self.index}]"


class Char(int):
    """A char value that behaves like int in arithmetic but prints as a char."""
    def __repr__(self):
        return chr(self) if 0 <= self < 0x110000 else str(int(self))


class Executor:
    MAX_STEPS = 100000

    def __init__(self, program: Program, input_lines: Optional[List[str]] = None):
        self.program = program
        self.global_scope: Dict[str, Tuple[str, Any]] = {}
        self.call_stack: List[Frame] = []
        self.output = ""
        self.history: List[Dict] = []
        self.step_count = 0
        self.input_buffer: List[str] = list(input_lines) if input_lines else []
        self.functions: Dict[str, FuncDef] = {}
        self._aborted = False
        self._error = None
        # track which indices have been written to for each array
        # (keyed by id(list)); used by the GUI to show only changed elements
        self.array_modified: Dict[int, set] = {}

    # ---- variable lookup ----
    def get_var(self, name: str):
        # search call stack top-down
        for frame in reversed(self.call_stack):
            if name in frame.locals:
                vtype, val = frame.locals[name]
                return val, vtype, frame.locals
        if name in self.global_scope:
            vtype, val = self.global_scope[name]
            return val, vtype, self.global_scope
        raise NameError(f"Undefined variable '{name}'")

    def _mark_array_written(self, arr, idx: int):
        """Mark an array index as having been written to (for display)."""
        if isinstance(arr, list) and 0 <= idx < len(arr):
            self.array_modified.setdefault(id(arr), set()).add(idx)

    def set_var(self, name: str, value, vtype_hint: str = ""):
        for frame in reversed(self.call_stack):
            if name in frame.locals:
                cur_type = frame.locals[name][0]
                frame.locals[name] = (cur_type, value)
                return
        if name in self.global_scope:
            cur_type = self.global_scope[name][0]
            self.global_scope[name] = (cur_type, value)
            return
        # declare implicitly as global (shouldn't happen normally)
        self.global_scope[name] = (vtype_hint or "int", value)

    def declare_var(self, name: str, vtype: str, value=None, array_size: Optional[int] = None):
        scope = self.call_stack[-1].locals if self.call_stack else self.global_scope
        if value is None:
            value = default_value(vtype)
        if array_size is not None:
            base = vtype.rstrip("*")
            zero = 0.0 if base in ("float", "double") else 0
            if isinstance(value, list):
                # initializer list: pad/truncate to array_size
                init_count = len(value)
                arr = list(value)
                if len(arr) < array_size:
                    arr += [zero] * (array_size - len(arr))
                else:
                    arr = arr[:array_size]
                value = arr
                # explicitly initialized indices count as "changed" for display
                for i in range(min(init_count, array_size)):
                    self._mark_array_written(arr, i)
            else:
                value = [zero] * array_size
        scope[name] = (vtype, value)

    # ---- snapshot ----
    _BUILTIN_NAMES = {"cout", "cin", "endl"}

    def _snapshot(self, line: int):
        # deep copy of variables
        def copy_scope(scope: Dict) -> Dict:
            out = {}
            for k, (vt, v) in scope.items():
                if k in self._BUILTIN_NAMES:
                    continue
                if isinstance(v, list):
                    entry = {"type": vt, "value": list(v)}
                    mod = self.array_modified.get(id(v))
                    entry["modified"] = sorted(mod) if mod else []
                    out[k] = entry
                else:
                    out[k] = {"type": vt, "value": v}
            return out

        snap = {
            "line": line,
            "output": self.output,
            "global": copy_scope(self.global_scope),
            "stack": [{"func": f.func_name, "vars": copy_scope(f.locals)} for f in self.call_stack],
            "aborted": self._aborted,
            "error": self._error,
        }
        self.history.append(snap)

    # ---- type coercion for arithmetic ----
    @staticmethod
    def _to_num(v):
        if isinstance(v, bool):
            return int(v)
        return v

    def eval_expr(self, node: Node):
        if node is None:
            return None
        if isinstance(node, Literal):
            if node.ltype == "initlist":
                return [self.eval_expr(item) for item in node.value]
            if node.ltype == "char":
                return Char(node.value)
            return node.value
        if isinstance(node, VarRef):
            val, _, _ = self.get_var(node.name)
            return val
        if isinstance(node, ArrayAccess):
            if isinstance(node.array, VarRef):
                arr, _, _ = self.get_var(node.array.name)
            else:
                arr = self.eval_expr(node.array)
            idx = int(self.eval_expr(node.index))
            return arr[idx]
        if isinstance(node, BinaryOp):
            return self._eval_binary(node)
        if isinstance(node, UnaryOp):
            return self._eval_unary(node)
        if isinstance(node, AssignOp):
            return self._eval_assign(node)
        if isinstance(node, FuncCall):
            return self._eval_func_call(node)
        if isinstance(node, TernaryOp):
            c = self.eval_expr(node.cond)
            if c:
                return self.eval_expr(node.true_expr)
            return self.eval_expr(node.false_expr)
        raise ValueError(f"Cannot evaluate node {type(node).__name__}")

    def _eval_binary(self, node: BinaryOp):
        op = node.op
        # short-circuit logical
        if op == "&&":
            l = self.eval_expr(node.left)
            if not l:
                return 0
            r = self.eval_expr(node.right)
            return 1 if r else 0
        if op == "||":
            l = self.eval_expr(node.left)
            if l:
                return 1
            r = self.eval_expr(node.right)
            return 1 if r else 0
        l = self.eval_expr(node.left)
        # C++ stream: cin >> x  (read into lvalue node.right)
        if op == ">>" and isinstance(l, _CinSentinel):
            target = node.right
            if self.input_buffer:
                token = self.input_buffer.pop(0).strip()
                if token != "":
                    try:
                        val = int(token)
                    except ValueError:
                        try:
                            val = float(token)
                        except ValueError:
                            val = token
                    self._set_lvalue(target, val)
            return l
        r = self.eval_expr(node.right)
        # C++ stream: cout << x  (print r; endl prints newline)
        if op == "<<" and isinstance(l, _CoutSentinel):
            if isinstance(r, _EndlSentinel):
                self.output += "\n"
            elif isinstance(r, Char):
                self.output += chr(int(r)) if 0 <= int(r) < 0x110000 else str(int(r))
            elif isinstance(r, bool):
                self.output += str(int(r))
            elif isinstance(r, float):
                self.output += f"{r:.6g}"
            else:
                self.output += str(r)
            return l
        # ---- pointer / iterator arithmetic ----
        if op in ("+", "-"):
            # array + int  or  int + array  -> iterator
            if isinstance(l, list) and isinstance(r, int):
                return _Iterator(l, r)
            if isinstance(r, list) and isinstance(l, int):
                return _Iterator(r, l)
            if isinstance(l, _Iterator):
                if isinstance(r, int):
                    if op == "+":
                        return _Iterator(l.array, l.index + r)
                    else:
                        return _Iterator(l.array, l.index - r)
                if isinstance(r, _Iterator) and op == "-":
                    return l.index - r.index
            if isinstance(r, _Iterator) and isinstance(l, int) and op == "+":
                return _Iterator(r.array, r.index + l)
            # array - array -> distance (only if same object)
            if isinstance(l, list) and isinstance(r, list) and op == "-":
                if l is r:
                    return 0
        l = self._to_num(l)
        r = self._to_num(r)
        if op == "+":
            if isinstance(l, str) or isinstance(r, str):
                return str(l) + str(r)
            return l + r
        if op == "-":
            return l - r
        if op == "*":
            return l * r
        if op == "/":
            if isinstance(l, int) and isinstance(r, int):
                return l // r if r != 0 else 0
            return l / r if r != 0 else 0
        if op == "%":
            return l % r if r != 0 else 0
        if op == "==":
            return 1 if l == r else 0
        if op == "!=":
            return 1 if l != r else 0
        if op == "<":
            return 1 if l < r else 0
        if op == ">":
            return 1 if l > r else 0
        if op == "<=":
            return 1 if l <= r else 0
        if op == ">=":
            return 1 if l >= r else 0
        if op == "&":
            return int(l) & int(r)
        if op == "|":
            return int(l) | int(r)
        if op == "^":
            return int(l) ^ int(r)
        if op == "<<":
            return int(l) << int(r)
        if op == ">>":
            return int(l) >> int(r)
        raise ValueError(f"Unknown binary op {op}")

    def _eval_unary(self, node: UnaryOp):
        op = node.op
        operand = node.operand
        if op in ("++", "--"):
            # must be a variable (or array access)
            name, arr_idx = self._lvalue_name(operand)
            cur, _, scope = self._get_lvalue_scope(operand)
            delta = 1 if op == "++" else -1
            if isinstance(cur, int):
                new_val = cur + delta
            else:
                new_val = (cur + delta) if isinstance(cur, float) else (int(cur) + delta)
            self._set_lvalue(operand, new_val)
            if node.prefix:
                return new_val
            return cur
        val = self.eval_expr(operand)
        if op == "-":
            return -val
        if op == "+":
            return +val
        if op == "!":
            return 0 if val else 1
        if op == "~":
            return ~int(val)
        if op == "*":
            # dereference pointer / iterator
            if isinstance(val, _Iterator):
                return val.array[val.index]
            return val
        if op == "&":
            # address-of: return 0
            return 0
        raise ValueError(f"Unknown unary op {op}")

    def _lvalue_name(self, node):
        if isinstance(node, VarRef):
            return node.name, None
        if isinstance(node, ArrayAccess):
            return node.array.name if isinstance(node.array, VarRef) else "", node.index
        return None, None

    def _get_lvalue_scope(self, node):
        """Return (current_value, type, scope_dict) for an lvalue."""
        if isinstance(node, VarRef):
            return self.get_var(node.name)
        if isinstance(node, ArrayAccess):
            arr, vt, scope = self.get_var(node.array.name)
            idx = int(self.eval_expr(node.index))
            return arr[idx], vt, (arr, idx)
        raise ValueError("Not an lvalue")

    def _set_lvalue(self, node, value):
        if isinstance(node, VarRef):
            self.set_var(node.name, value)
        elif isinstance(node, ArrayAccess):
            arr, _, _ = self.get_var(node.array.name)
            idx = int(self.eval_expr(node.index))
            self._mark_array_written(arr, idx)
            arr[idx] = value
        else:
            raise ValueError("Cannot assign to non-lvalue")

    def _eval_assign(self, node: AssignOp):
        op = node.op
        value = self.eval_expr(node.value)
        if op == "=":
            self._set_lvalue(node.target, value)
            return value
        cur, _, _ = self._get_lvalue_scope(node.target)
        base_op = op[:-1]
        if base_op == "+":
            new_val = cur + value
        elif base_op == "-":
            new_val = cur - value
        elif base_op == "*":
            new_val = cur * value
        elif base_op == "/":
            if isinstance(cur, int) and isinstance(value, int):
                new_val = cur // value if value != 0 else 0
            else:
                new_val = cur / value if value != 0 else 0
        elif base_op == "%":
            new_val = cur % value if value != 0 else 0
        elif base_op == "&":
            new_val = int(cur) & int(value)
        elif base_op == "|":
            new_val = int(cur) | int(value)
        elif base_op == "^":
            new_val = int(cur) ^ int(value)
        elif base_op == "<<":
            new_val = int(cur) << int(value)
        elif base_op == ">>":
            new_val = int(cur) >> int(value)
        else:
            new_val = value
        self._set_lvalue(node.target, new_val)
        return new_val

    def _eval_func_call(self, node: FuncCall):
        name = node.name
        # member call: obj.method(args)
        if node.target is not None:
            return self._eval_member_call(node)
        # scanf needs raw AST nodes (lvalues), not evaluated values
        if name == "scanf":
            return self._builtin_scanf(node.args)
        args = [self.eval_expr(a) for a in node.args]
        if name == "printf":
            return self._builtin_printf(args)
        if name == "puts":
            self.output += str(args[0]) + "\n"
            return 0
        if name == "putchar":
            c = args[0]
            self.output += chr(int(c)) if isinstance(c, (int, float)) else str(c)
            return int(c) if isinstance(c, (int, float)) else 0
        if name == "getchar":
            if self.input_buffer:
                s = self.input_buffer.pop(0)
                if s:
                    return ord(s[0])
            return -1
        if name == "abs":
            return abs(int(args[0]))
        if name == "sqrt":
            import math
            return math.sqrt(float(args[0]))
        if name == "pow":
            return float(args[0]) ** float(args[1])
        if name == "strlen":
            return len(str(args[0]))
        # ---- STL algorithms ----
        if name in ("sort", "stable_sort"):
            return self._stl_sort(args)
        if name == "reverse":
            return self._stl_reverse(args)
        if name == "swap":
            return self._stl_swap(node.args)
        if name in ("max_element", "min_element"):
            return self._stl_minmax_element(args, name == "max_element")
        if name == "find":
            return self._stl_find(args)
        if name == "count":
            return self._stl_count(args)
        if name in ("fill",):
            return self._stl_fill(args)
        if name in ("accumulate",):
            return self._stl_accumulate(args)
        if name in self.functions:
            return self._call_user_func(name, args)
        raise NameError(f"Undefined function '{name}'")

    def _eval_member_call(self, node: FuncCall):
        obj = self.eval_expr(node.target)
        name = node.name
        args = [self.eval_expr(a) for a in node.args]
        # array/string member functions
        if name == "begin":
            if isinstance(obj, list):
                return _Iterator(obj, 0)
            if isinstance(obj, str):
                return _Iterator(list(obj), 0)
            return _Iterator([], 0)
        if name == "end":
            if isinstance(obj, (list, str)):
                n = len(obj)
                return _Iterator(obj if isinstance(obj, list) else list(obj), n)
            return _Iterator([], 0)
        if name == "size" or name == "length":
            return len(obj) if hasattr(obj, "__len__") else 0
        if name == "empty":
            return 1 if (hasattr(obj, "__len__") and len(obj) == 0) else 0
        if name == "front":
            return obj[0] if isinstance(obj, list) and obj else 0
        if name == "back":
            return obj[-1] if isinstance(obj, list) and obj else 0
        if name == "push_back":
            if isinstance(obj, list):
                obj.append(args[0] if args else 0)
                self._mark_array_written(obj, len(obj) - 1)
            return None
        if name == "pop_back":
            if isinstance(obj, list) and obj:
                obj.pop()
            return None
        if name == "clear":
            if isinstance(obj, list):
                obj.clear()
            return None
        if name == "at":
            if isinstance(obj, list) and args:
                idx = int(args[0])
                return obj[idx]
            return 0
        # fallback: ignore unknown member call
        return None

    # ---- STL algorithm implementations ----
    @staticmethod
    def _iter_range(args):
        """Extract (array, start, end) from iterator args [begin, end]."""
        it1, it2 = args[0], args[1]
        if isinstance(it1, _Iterator) and isinstance(it2, _Iterator):
            return it1.array, it1.index, it2.index
        return None, 0, 0

    def _stl_sort(self, args):
        arr, b, e = self._iter_range(args)
        if arr is None:
            return None
        sub = sorted(arr[b:e])
        arr[b:e] = sub
        for i in range(b, e):
            self._mark_array_written(arr, i)
        return None

    def _stl_reverse(self, args):
        arr, b, e = self._iter_range(args)
        if arr is None:
            return None
        arr[b:e] = arr[b:e][::-1]
        for i in range(b, e):
            self._mark_array_written(arr, i)
        return None

    def _stl_swap(self, arg_nodes):
        # swap(a, b) — a, b are lvalue AST nodes (VarRef or ArrayAccess)
        if len(arg_nodes) != 2:
            return None
        a_node, b_node = arg_nodes[0], arg_nodes[1]
        # if both are iterators (e.g. swap of *it1, *it2 not supported; but
        # swap(a.begin(), b.begin()) is unusual). Handle lvalues:
        try:
            val_a, _, _ = self._get_lvalue_scope(a_node)
        except Exception:
            val_a = self.eval_expr(a_node)
        try:
            val_b, _, _ = self._get_lvalue_scope(b_node)
        except Exception:
            val_b = self.eval_expr(b_node)
        try:
            self._set_lvalue(a_node, val_b)
            self._set_lvalue(b_node, val_a)
        except Exception:
            pass
        return None

    def _stl_minmax_element(self, args, is_max):
        arr, b, e = self._iter_range(args)
        if arr is None:
            return _Iterator([], 0)
        sub = arr[b:e]
        if not sub:
            return _Iterator(arr, b)
        val = max(sub) if is_max else min(sub)
        idx = sub.index(val) + b
        return _Iterator(arr, idx)

    def _stl_find(self, args):
        arr, b, e = self._iter_range(args[:2])
        if arr is None:
            return _Iterator([], 0)
        target = args[2] if len(args) > 2 else None
        sub = arr[b:e]
        if target in sub:
            idx = sub.index(target) + b
            return _Iterator(arr, idx)
        return _Iterator(arr, e)

    def _stl_count(self, args):
        arr, b, e = self._iter_range(args[:2])
        if arr is None:
            return 0
        target = args[2] if len(args) > 2 else None
        return arr[b:e].count(target)

    def _stl_fill(self, args):
        arr, b, e = self._iter_range(args[:2])
        if arr is None:
            return None
        val = args[2] if len(args) > 2 else 0
        for i in range(b, e):
            arr[i] = val
            self._mark_array_written(arr, i)
        return None

    def _stl_accumulate(self, args):
        arr, b, e = self._iter_range(args[:2])
        if arr is None:
            return 0
        init = args[2] if len(args) > 2 else 0
        total = init
        for v in arr[b:e]:
            total += v
        return total

    def _builtin_printf(self, args):
        if not args:
            return 0
        fmt = str(args[0])
        vals = args[1:]
        out = []
        vi = 0
        i = 0
        while i < len(fmt):
            c = fmt[i]
            if c == "%" and i + 1 < len(fmt):
                j = i + 1
                # skip flags/width/precision
                while j < len(fmt) and fmt[j] in "-+ #0":
                    j += 1
                while j < len(fmt) and fmt[j].isdigit():
                    j += 1
                if j < len(fmt) and fmt[j] == ".":
                    j += 1
                    while j < len(fmt) and fmt[j].isdigit():
                        j += 1
                if j < len(fmt) and fmt[j] in "hl":
                    j += 1
                spec = fmt[j] if j < len(fmt) else "%"
                width = ""
                # find width simply
                m = re.match(r"%([-+ #0]*)(\d*)(\.\d+)?[hl]?([%difFeEgGcCsSuxXo])", fmt[i:j+1])
                if m:
                    flags, w, prec, sp = m.groups()
                    width = w
                    precision = prec[1:] if prec else ""
                else:
                    sp = spec
                    width = ""
                    precision = ""
                if sp == "%":
                    out.append("%")
                elif sp in ("d", "i"):
                    val = int(vals[vi]) if vi < len(vals) else 0
                    vi += 1
                    s = str(val)
                    if width:
                        s = s.rjust(int(width))
                    out.append(s)
                elif sp in ("f", "F", "e", "E", "g", "G"):
                    val = float(vals[vi]) if vi < len(vals) else 0.0
                    vi += 1
                    if precision:
                        s = f"{val:.{int(precision)}f}"
                    else:
                        s = f"{val:.6f}"
                    if width:
                        s = s.rjust(int(width))
                    out.append(s)
                elif sp == "c":
                    val = vals[vi] if vi < len(vals) else 0
                    vi += 1
                    if isinstance(val, (int, float)):
                        out.append(chr(int(val)))
                    else:
                        out.append(str(val))
                elif sp in ("s", "S"):
                    val = vals[vi] if vi < len(vals) else ""
                    vi += 1
                    out.append(str(val))
                elif sp in ("x", "X"):
                    val = int(vals[vi]) if vi < len(vals) else 0
                    vi += 1
                    s = format(val, "x" if sp == "x" else "X")
                    if width:
                        s = s.rjust(int(width))
                    out.append(s)
                elif sp == "o":
                    val = int(vals[vi]) if vi < len(vals) else 0
                    vi += 1
                    out.append(format(val, "o"))
                elif sp == "u":
                    val = int(vals[vi]) if vi < len(vals) else 0
                    vi += 1
                    out.append(str(val & 0xFFFFFFFF))
                else:
                    out.append(fmt[i:j+1])
                i = j + 1
                continue
            out.append(c)
            i += 1
        self.output += "".join(out)
        return 0

    def _builtin_scanf(self, arg_nodes):
        # arg_nodes are raw AST nodes: format string + lvalue pointers
        if not arg_nodes:
            return 0
        fmt = str(self.eval_expr(arg_nodes[0]))
        ptrs = arg_nodes[1:]
        count = 0
        for pt in ptrs:
            # pt should be a VarRef or UnaryOp(& ...)
            target = None
            if isinstance(pt, VarRef):
                target = pt
            elif isinstance(pt, UnaryOp) and pt.op == "&":
                target = pt.operand
            if target is None:
                continue
            if not self.input_buffer:
                break
            token = self.input_buffer.pop(0).strip()
            if token == "":
                break
            # determine type from the variable's declared type
            cur, vt, _ = self._get_lvalue_scope(target)
            if "float" in vt or "double" in vt:
                val = float(token)
            else:
                try:
                    val = int(token)
                except ValueError:
                    val = token
            self._set_lvalue(target, val)
            count += 1
        return count

    def _call_user_func(self, name: str, args: list):
        fd = self.functions[name]
        frame = Frame(name)
        self.call_stack.append(frame)
        # bind params
        for (ptype, pname), arg_val in zip(fd.params, args):
            frame.locals[pname] = (ptype, arg_val)
        # if fewer args than params, fill defaults
        for i in range(len(args), len(fd.params)):
            ptype, pname = fd.params[i]
            frame.locals[pname] = (ptype, default_value(ptype))
        ret_val = None
        try:
            if fd.body is not None:
                self.execute_block(fd.body, record_first=True)
        except ReturnException as re_:
            ret_val = re_.value
        self.call_stack.pop()
        return ret_val

    # ---- statement execution ----
    def execute_block(self, block: Block, record_first: bool = True):
        for i, stmt in enumerate(block.stmts):
            rec = record_first or i > 0
            self.execute_stmt(stmt, record=rec)

    def execute_stmt(self, stmt: Node, record: bool = True):
        if self.step_count > self.MAX_STEPS:
            self._aborted = True
            self._error = "Maximum step count exceeded (possible infinite loop)"
            return
        if isinstance(stmt, _MultiDecl):
            for i, d in enumerate(stmt.decls):
                self._decl_one(d)
                if record or i > 0:
                    self._snapshot(d.line)
            return
        if isinstance(stmt, VarDecl):
            self._decl_one(stmt)
            if record:
                self._snapshot(stmt.line)
            return
        if isinstance(stmt, ExprStmt):
            self.step_count += 1
            if stmt.expr is not None:
                self.eval_expr(stmt.expr)
            if record:
                self._snapshot(stmt.line)
            return
        if isinstance(stmt, Block):
            self.execute_block(stmt, record_first=record)
            return
        if isinstance(stmt, IfStmt):
            self.step_count += 1
            self._snapshot(stmt.line)
            cond = self.eval_expr(stmt.cond)
            if cond:
                self.execute_stmt(stmt.then_branch, record=False)
            elif stmt.else_branch is not None:
                self.execute_stmt(stmt.else_branch, record=False)
            return
        if isinstance(stmt, WhileStmt):
            while True:
                self.step_count += 1
                self._snapshot(stmt.line)
                if self.step_count > self.MAX_STEPS:
                    self._aborted = True
                    self._error = "Maximum step count exceeded"
                    return
                cond = self.eval_expr(stmt.cond)
                if not cond:
                    break
                try:
                    self.execute_stmt(stmt.body, record=False)
                except BreakException:
                    break
                except ContinueException:
                    continue
            return
        if isinstance(stmt, DoWhileStmt):
            while True:
                try:
                    self.execute_stmt(stmt.body, record=False)
                except BreakException:
                    break
                except ContinueException:
                    pass
                self.step_count += 1
                self._snapshot(stmt.line)
                cond = self.eval_expr(stmt.cond)
                if not cond:
                    break
            return
        if isinstance(stmt, ForStmt):
            # init
            if stmt.init is not None:
                self.execute_stmt(stmt.init, record=record)
            while True:
                self.step_count += 1
                self._snapshot(stmt.line)
                if self.step_count > self.MAX_STEPS:
                    self._aborted = True
                    self._error = "Maximum step count exceeded"
                    return
                if stmt.cond is not None:
                    cond = self.eval_expr(stmt.cond)
                    if not cond:
                        break
                try:
                    self.execute_stmt(stmt.body, record=False)
                except BreakException:
                    break
                except ContinueException:
                    pass
                if stmt.update is not None:
                    self.eval_expr(stmt.update)
            return
        if isinstance(stmt, ReturnStmt):
            self.step_count += 1
            val = None
            if stmt.expr is not None:
                val = self.eval_expr(stmt.expr)
            if record:
                self._snapshot(stmt.line)
            raise ReturnException(val)
        if isinstance(stmt, BreakStmt):
            self.step_count += 1
            if record:
                self._snapshot(stmt.line)
            raise BreakException()
        if isinstance(stmt, ContinueStmt):
            self.step_count += 1
            if record:
                self._snapshot(stmt.line)
            raise ContinueException()
        raise ValueError(f"Unknown statement {type(stmt).__name__}")

    def _decl_one(self, d: VarDecl):
        self.step_count += 1
        arr_size = None
        if d.array_size is not None:
            arr_size = int(self.eval_expr(d.array_size))
        init_val = None
        if d.init is not None:
            init_val = self.eval_expr(d.init)
        # non-array scalar init from braced list: int a = {5}; -> a = 5
        if arr_size is None and isinstance(init_val, list):
            init_val = init_val[0] if init_val else default_value(d.vtype)
        self.declare_var(d.name, d.vtype, init_val, arr_size)

    # ---- run ----
    def run(self):
        # register C++ stream builtins
        self.global_scope["cout"] = ("ostream", _CoutSentinel())
        self.global_scope["cin"] = ("istream", _CinSentinel())
        self.global_scope["endl"] = ("manipulator", _EndlSentinel())
        # collect functions and global vars
        for decl in self.program.decls:
            if isinstance(decl, FuncDef):
                self.functions[decl.name] = decl
            elif isinstance(decl, VarDecl):
                self._decl_one(decl)
            elif isinstance(decl, _MultiDecl):
                for d in decl.decls:
                    self._decl_one(d)
        # initial snapshot (line 0, nothing executed yet)
        self._snapshot(0)
        # run main
        if "main" not in self.functions:
            self._error = "No 'main' function found"
            self._snapshot(0)
            return
        try:
            self._call_user_func("main", [])
        except ReturnException:
            pass
        except Exception as e:
            self._error = str(e)
        # final snapshot
        self._snapshot(0)


def interpret(code: str, input_lines: Optional[List[str]] = None) -> Executor:
    tokens = tokenize(code)
    parser = Parser(tokens)
    program = parser.parse_program()
    exe = Executor(program, input_lines=input_lines)
    exe.run()
    return exe


if __name__ == "__main__":
    sample = """
#include <stdio.h>
int main() {
    int a = 5;
    int b = 10;
    int c = a + b;
    printf("sum = %d\\n", c);
    for (int i = 0; i < 3; i++) {
        printf("i = %d\\n", i);
    }
    return 0;
}
"""
    exe = interpret(sample)
    for i, s in enumerate(exe.history):
        print(f"Step {i}: line={s['line']} output={s['output']!r}")
        print(f"  vars: {s['stack']}")
