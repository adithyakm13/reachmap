from django.db import models


class Customer(models.Model):
    name = models.CharField(max_length=120)


class Invoice(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    number = models.CharField(max_length=20)
    amount_cents = models.IntegerField()
    settled = models.BooleanField(default=False)
