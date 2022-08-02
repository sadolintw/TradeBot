import json

from django.http import HttpResponse
# noinspection PyUnresolvedReferences
from rest_framework.decorators import api_view


def index(request):
    return HttpResponse("Hello, world. You're at the polls index.")


@api_view(['GET', 'POST'])
def webhook(request):
    signal = json.loads(request.body)
    print(request, json.dumps(signal))
    return HttpResponse('received')
