"""Structured knowledge inventory tool backed by MySQL metadata tables."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from config.settings import get_settings
from tools.base_tool import BaseTool, ToolException


class KnowledgeInventoryTool(BaseTool):
    """List imported knowledge files from relational metadata."""

    @property
    def name(self) -> str:
        return "knowledge_inventory"

    @property
    def description(self) -> str:
        return (
            "List the actual imported knowledge files from MySQL metadata. "
            "Use this for inventory questions such as what files, PDFs, "
            "documents, or manuals are currently in the knowledge base."
        )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional knowledge_document status filter, for example ready or pending.",
                }
            },
            "required": [],
        }

    def _connect(self):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except Exception as exc:
            raise ToolException(
                code="MYSQL_DRIVER_MISSING",
                message="pymysql is required for knowledge inventory queries",
            ) from exc

        settings = get_settings()
        return pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=settings.mysql_database,
            charset="utf8mb4",
            cursorclass=DictCursor,
        )

    async def _execute(self, status: Optional[str] = None) -> Dict[str, Any]:
        conn = None
        try:
            conn = self._connect()
            sql = """
                SELECT
                    m.id AS manual_id,
                    m.manual_name,
                    d.document_id,
                    d.file_name,
                    d.file_type,
                    d.file_size,
                    d.minio_object_name,
                    d.status,
                    d.text_count,
                    d.image_count,
                    d.table_count,
                    d.created_at
                FROM maintenance_manual m
                LEFT JOIN knowledge_document d
                    ON m.active_document_id = d.id
                WHERE (%s IS NULL OR d.status = %s)
                ORDER BY m.created_at DESC, d.created_at DESC
            """
            with conn.cursor() as cursor:
                cursor.execute(sql, (status, status))
                rows = cursor.fetchall()
            documents = [self._normalize_row(row) for row in rows]
            return {
                "total": len(documents),
                "documents": documents,
                "source": "mysql:maintenance_manual+knowledge_document",
                "answer_policy": (
                    "Use only these structured rows for knowledge inventory answers. "
                    "Display manual_name as the document name. Do not display file_name."
                ),
            }
        except ToolException:
            raise
        except Exception as exc:
            raise ToolException(code="MYSQL_QUERY_FAILED", message=str(exc)) from exc
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
        def normalize(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat(sep=" ")
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, Decimal):
                return int(value) if value == value.to_integral_value() else float(value)
            return value

        data = {key: normalize(value) for key, value in dict(row).items()}
        data.pop("file_name", None)
        return data


_inventory_tool: Optional[KnowledgeInventoryTool] = None


def get_knowledge_inventory_tool() -> KnowledgeInventoryTool:
    global _inventory_tool
    if _inventory_tool is None:
        _inventory_tool = KnowledgeInventoryTool()
    return _inventory_tool
