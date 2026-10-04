import base64
import json

from evograph.application.attachments import AttachmentService
from evograph.domain.models import Attachment
from evograph.domain.plan_contracts import IntentSource, Requirement
from evograph.infrastructure.database import Database


def source_contract(app):
    p=app.projects.create('Attachment contract')
    p.unified_planning=True
    p.plan_contract.sources=[IntentSource(id='s',text='Preserve the original requirement')]
    p.plan_contract.requirements=[Requirement(id='r',source_id='s',quote='Preserve the original requirement')]
    app.db.save(p,'fixture')
    return p


def check_stored(app,p,expected):
    with app.db.connect() as con:
        raw=json.loads(con.execute('select payload from projects where id=?',(p.id,)).fetchone()[0])
        assert 'plan_contract' not in raw and 'unified_planning' not in raw
    reopened=Database(app.db.path).get(p.id)
    assert reopened.unified_planning
    assert reopened.plan_contract==expected


def test_upload_keeps_extensions_and_legacy_core_schema(app):
    p=source_contract(app)
    app.attachments.upload(p.id,'fixture.txt',base64.b64encode(b'Plain fixture').decode())
    check_stored(app,p,p.plan_contract)


def test_attachment_dedup_migration_keeps_extensions_and_core_schema(app):
    p=source_contract(app)
    a=Attachment(name='a.txt',media_type='text/plain',size=1,excerpt='A')
    b=Attachment(name='b.txt',media_type='text/plain',size=1,excerpt='A')
    p.attachments=[a,b]
    app.db.save(p,'fixture')
    with app.db.connect() as con:
        for asset in (a,b):
            con.execute('insert into attachments values(?,?,?)',(asset.id,p.id,b'A'))
    AttachmentService(app.db)
    assert len(app.db.get(p.id).attachments)==1
    check_stored(app,p,p.plan_contract)
